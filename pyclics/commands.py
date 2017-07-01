# coding: utf8
from __future__ import unicode_literals, print_function, division
from collections import defaultdict
from itertools import combinations

from six import PY2
from clldutils.clilib import command
from clldutils.path import remove, write_text
from clldutils.markup import Table
from clldutils import jsonlib
from pyconcepticon.api import Concepticon
from lingpy.convert.graph import networkx2igraph
import networkx as nx

from pyclics.utils import (
    load_concepticon, full_colexification, make_language_map, partial_colexification,
)
from pyclics.providers import LOADER
from pyclics.api import Network


@command(name='list')
def list_(args):
    """List datasets available for loading

    clics list
    """
    for loader in sorted(LOADER, key=lambda cls: cls.id()):
        print(loader.id())


@command()
def load(args):
    """
    clics load [DATASET]+
    """
    for cls in LOADER:
        if cls.id() in args.args:
            c = cls(args.api, args.lexibank_repos, args.glottolog_repos, log=args.log)()
            args.log.info('provider {0}: {1} wordlists loaded'.format(cls.id(), c))


@command()
def clean(args):
    """Removes all loaded lexical data

    clics clean
    """
    for p in args.api.path('cldf').iterdir():
        remove(p)


@command()
def languages(args):
    data = {}
    ltable = Table('Number', 'Language', 'Family', 'Size', 'Source')
    with args.api.csv_writer('stats', 'languages') as writer:
        writer.writerow([
            'Identifier',
            'Language_name',
            'Language_ID',
            'Family',
            'Longitude',
            'Latitude'])
        count, nan = 1, 1
        for i, wl in args.api.iter_wordlists(args.glottolog_repos, args.log):
            if wl['meta']['family'] == '':
                wl['meta']['family'] = 'NAN-{0}'.format(nan)
                nan += 1
                args.api.write_wordlist(wl, wl['meta']['identifier'])
            if wl['meta']['longitude']:
                writer.writerow([
                    wl['meta']['identifier'],
                    wl['meta']['name'],
                    wl['meta']['glottocode'],
                    wl['meta']['family'],
                    wl['meta']['longitude'],
                    wl['meta']['latitude']])
                wl['meta']['size'] = len(wl['identifiers'])
                data[wl['meta']['identifier']] = wl['meta']

                ltable.append([
                    count,
                    '[{0}](http://glottolog.org/resource/languoid/id/{1})'.format(
                        wl['meta']['name'], wl['meta']['glottocode']),
                    wl['meta']['family'],
                    wl['meta']['size'],
                    wl['meta']['source'],
                ])
                count += 1

    args.api.write_md_table('cldf', 'README', 'Languages in CLICS', ltable, args.log)
    jsonlib.dump(make_language_map(data), args.api.path('geo', 'languages.geojson'))


@command()
def coverage(args):
    concepticon = load_concepticon(args.api, Concepticon(args.concepticon_repos))
    concepts = defaultdict(list)
    languages = [line[0] for line in args.api.csv_reader('stats', 'languages')]

    with args.api.csv_writer('data', 'words') as writer:
        writer.writerow([
            'WordID',
            'ConcepticonId',
            'ConcepticonGloss',
            'Gloss',
            'LanguageId',
            'LanguageName',
            'Family',
            'Value',
            'ClicsValue'])
        for i, wl in args.api.iter_wordlists(args.glottolog_repos, args.log):
            if wl['meta']['identifier'] in languages:
                cidx, vidx, oidx, gidx = (
                    wl[0].index('Parameter_ID'),
                    wl[0].index('Clics_Value'),
                    wl[0].index('Value'),
                    wl[0].index('Parameter_name'))
                for idx in wl['identifiers']:
                    concept = wl[idx][cidx]
                    value = wl[idx][vidx]

                    if concept and concepticon[concept].get('gloss', ''):
                        concepts[concept].append(
                            (wl['meta']['family'], wl['meta']['identifier'], idx))
                        writer.writerow([
                            idx,
                            concept,
                            concepticon[concept]['gloss'],
                            wl[idx][gidx],
                            wl['meta']['glottocode'],
                            wl['meta']['name'],
                            wl['meta']['family'],
                            wl[idx][oidx],
                            value])

    concept_table = Table('Number', 'Concept', 'SemanticField', 'Category', 'Reflexes')
    with args.api.csv_writer('stats', 'concepts') as writer:
        writer.writerow([
            'ID',
            'Gloss',
            'Semanticfield',
            'Category',
            'WordFrequency',
            'LanguageFrequency',
            'FamilyFrequency',
            'Words',
            'Languages',
            'Families',
        ])
        for i, (concept, lists) in enumerate(concepts.items()):
            writer.writerow([
                concept,
                concepticon[concept]['gloss'],
                concepticon[concept]['semanticfield'],
                concepticon[concept]['ontological_category'],
                len(set([x[0] for x in lists])),
                len(set([x[1] for x in lists])),
                len(lists),
                ';'.join(sorted(set([x[2] for x in lists]))),
                ';'.join(sorted(set([x[1] for x in lists]))),
                ';'.join(sorted(set([x[0] for x in lists])))])
            concept_table.append([
                i + 1,
                '[{0}](http://concepticon.clld.org/parameters/{1})'.format(
                    concepticon[concept]['gloss'], concept),
                concepticon[concept]['semanticfield'],
                concepticon[concept]['ontological_category'],
                len(lists)])

    args.api.write_md_table(
        'stats', 'concepts', 'Concepts in CLICS', concept_table, args.log)
    return concepts


@command()
def colexification(args):
    threshold = args.threshold or 1
    edgefilter = args.edgefilter
    languages = [line[0] for line in args.api.csv_reader('stats', 'languages')]

    _tmp = list(args.api.csv_reader('stats', 'concepts'))
    concepts = {x[0]: dict(zip(_tmp[0], x)) for x in _tmp[1:]}
    G = nx.Graph()
    for idx, vals in concepts.items():
        vals['ConcepticonId'] = vals['ID']
        G.add_node(idx, **vals)

    for i, wl in args.api.iter_wordlists(args.glottolog_repos, args.log):
        if wl['meta']['identifier'] in languages:
            cols = full_colexification(
                wl,
                key='Parameter_ID',
                entry='Clics_Value',
                indices='identifiers')
            for k, v in cols.items():
                for (conceptA, idxA), (conceptB, idxB) in combinations(v, r=2):
                    # check for identical concept resulting from word-variants
                    if conceptA != conceptB:
                        if G.edge.get(conceptA, {}).get(conceptB, False):
                            G.edge[conceptA][conceptB]['words'].add((idxA, idxB))
                            G.edge[conceptA][conceptB]['languages'].add(wl['meta']['identifier'])
                            G.edge[conceptA][conceptB]['families'].add(wl['meta']['family'])
                        else:
                            G.add_edge(
                                conceptA,
                                conceptB,
                                words={(idxA, idxB)},
                                languages={wl['meta']['identifier']},
                                families={wl['meta']['family']})
    ignore_edges = []
    with args.api.csv_writer('stats', 'colexifications-{0}-{1}'.format(
            threshold, edgefilter)) as f:
        f.writerow('EdgeA,EdgeB,FamilyWeight,LanguageWeight,WordWeight'.split())
        for edgeA, edgeB, data in G.edges(data=True):
            data['WordWeight'] = len(data['words'])
            data['words'] = ';'.join(
                sorted(['{0}/{1}'.format(x, y) for x, y in data['words']]))
            data['FamilyWeight'] = len(data['families'])
            data['families'] = ';'.join(sorted(data['families']))
            data['LanguageWeight'] = len(data['languages'])
            data['languages'] = ';'.join(data['languages'])
            if edgefilter == 'families' and data['FamilyWeight'] < threshold:
                ignore_edges.append((edgeA, edgeB))
            elif edgefilter == 'languages' and data['LanguageWeight'] < threshold:
                ignore_edges.append((edgeA, edgeB))
            elif edgefilter == 'words' and data['WordWeight'] < threshold:
                ignore_edges.append((edgeA, edgeB))
            f.writerow([
                '{0}'.format(x) for x in [
                    edgeA,
                    edgeB,
                    data['FamilyWeight'],
                    data['LanguageWeight'],
                    data['WordWeight']]])

    G.remove_edges_from(ignore_edges)
    args.api.save_graph(
        G, args.graphname or 'network', threshold, edgefilter, log=args.log)


@command()
def articulationpoints(args):
    """Compute articulation points in subgraphs of the graph.
    
    Parameters
    ----------
    graphname : str
        Refers to pre-computed graphs stored in folder, with the name being the
        first element.
    edgefilter : str (default="families")
        Refers to second component of filename, thus, the component managing
        how edges are created and defined (here: language families as a
        default).
    subgraph : str (default="infomap")
        Determines the name of the subgraph that is used to pre-filter the
        search for articulation points. Defaults to the infomap-algorithms.
    threshold : int (default=1)
        The threshold which was used to calculate the community detection
        analaysis.

    Note
    ----
    Method searches for articulation points inside partitions of the graph,
    usually the partitions as provided by the infomap algorithm. The repository
    stores graph data in different forms, as binary graph and as gml, and the
    paramters are used to identify a given analysis by its filename and make
    sure the correct graph is loaded.
    """
    threshold = args.threshold or 1
    graphname = args.graphname or 'network'

    graph = args.api.load_graph(graphname, threshold, args.edgefilter)
    coms = defaultdict(list)
    for node, data in graph.nodes(data=True):
        coms[data['infomap']].append(node)
    _tmp = []
    for com, nodes in sorted(coms.items(), key=lambda x: len(x), reverse=True):
        if len(nodes) > 5:
            subgraph = graph.subgraph(nodes)
            degrees = subgraph.degree(list(subgraph.nodes()))
            cnodes = sorted(degrees, key=lambda x: degrees[x], reverse=True)
            cnode = cnodes[0]
            graph.node[cnode]['DegreeCentrality'] = 1
            artipoints = nx.articulation_points(subgraph)
            for artip in artipoints:
                if 'ArticulationPoint' in graph.node[artip]:
                    graph.node[artip]['ArticulationPoint'] += 1
                else:
                    graph.node[artip]['ArticulationPoint'] = 1
                if bool(args.verbosity):
                    print('{0}\t{1}\t{2}'.format(
                        com, graph.node[cnode]['Gloss'], graph.node[artip]['Gloss']))
                _tmp.append((
                    artip, graph.node[artip]['Gloss'],
                    com,
                    cnode,
                    graph.node[cnode]['Gloss'],
                    len(nodes)))
            if bool(args.verbosity):
                print('')
    for node, data in graph.nodes(data=True):
        data.setdefault('ArticulationPoint', 0)
        data.setdefault('DegreeCentrality', 0)

    aps = Network('articulationpoints', threshold, args.edgefilter)
    ap_table = Table('Number', 'Concept', 'Community', 'CommunitySize', 'CentralNode')
    with args.api.csv_writer('stats', '{0}'.format(aps)) as writer:
        writer.writerow([
            'ConcepticonId',
            'ConcepticonGloss',
            'Community',
            'CommunitySize',
            'CentralNode',
            'CentralNodeGloss'])
        for i, line in enumerate(_tmp):
            writer.writerow(line)
            ap_table.append([
                i + 1,
                '[{0[1]}]({0[0]})'.format(line),
                line[2],
                line[5],
                '[{0[4]}]({0[3]})'.format(line)])

    args.api.write_md_table(
        'stats',
        '{0}'.format(aps),
        'Articulation Points (Analysis {0} / {1})'.format(threshold, args.edgefilter),
        ap_table,
        args.log)
    args.api.save_graph(graph, aps, log=args.log)


@command()
def cocitationgraph(args):
    graph = args.api.load_graph(
        args.graphname or 'dinetwork', args.threshold or 3, args.edgefilter)
    igr = networkx2igraph(graph)
    v = igr.bibcoupling()
    return v, igr, graph


@command()
def transitions(args):
    threshold = args.threshold or 3
    edgefilter = args.edgefilter
    verbose = True
    aspect = args.aspect or 'semanticfield'
    weight = args.weight
    assymetric = True
    concepticon = load_concepticon(args.api, Concepticon(args.concepticon_repos))
    graph = args.api.load_graph(args.graphname or 'network', threshold, edgefilter)

    trs = defaultdict(list)
    for nA, nB, data in graph.edges(data=True):
        catA = concepticon[nA].get(aspect, '?')
        catB = concepticon[nB].get(aspect, '?')
        trs[catA, catB] += [(nA, nB, data[weight])]

    out = ['# Transitions (Analysis t={0}, f={1})'.format(threshold, edgefilter)]
    for (catA, catB), values in sorted(
            trs.items(), key=lambda x: len(x[1]), reverse=True):
        if len(values) > 2:
            if (assymetric and catA != catB) or not assymetric:
                if verbose:
                    print('{0:40} -> {1:40} / {2}'.format(catA, catB, len(values)))
                out.append('\n## {0} -> {1} / {2}'.format(catA, catB, len(values)))
                for a, b, c in values:
                    out.append('{0:20} -> {1:20} / {2}'.format(
                        concepticon[a]['GLOSS'], concepticon[b]['GLOSS'], c))
    write_text(
        args.api.path('stats', 'transitions-{0}-{1}.md'.format(threshold, edgefilter)),
        '\n'.join(out))


@command()
def communities(args):
    graphname = args.graphname or 'network'
    edge_weights = args.weight
    vertex_weights = 'FamilyFrequency'
    verbose = bool(args.verbosity)
    normalize = args.normalize
    edgefilter = args.edgefilter
    threshold = args.threshold or 1

    _graph = args.api.load_graph(graphname, threshold, edgefilter)
    for n, d in _graph.nodes(data=True):
        d[vertex_weights] = int(d[vertex_weights])

    if normalize:
        for edgeA, edgeB, data in _graph.edges(data=True):
            data[b'weight' if PY2 else 'weight'] = data[edge_weights] ** 2 / (
                _graph.node[edgeA][vertex_weights] +
                _graph.node[edgeB][vertex_weights] -
                data[edge_weights])
        vertex_weights = None
        edge_weights = b'weight' if PY2 else 'weight'
        if verbose:
            print('[i] computed weights')

    graph = networkx2igraph(_graph)
    if verbose:
        print('[i] converted graph...')
    comps = graph.community_infomap(
        edge_weights=edge_weights, vertex_weights=vertex_weights)
    D = {}
    with args.api.csv_writer('communities', 'infomap') as writer:
        for i, comp in enumerate(comps.subgraphs()):
            vertices = [v['name'] for v in comp.vs]
            for vertex in vertices:
                if verbose:
                    print(graph.vs[vertex]['Gloss'], i+1)
                D[graph.vs[vertex]['ConcepticonId']] = i+1
                writer.writerow([
                    graph.vs[vertex]['ConcepticonId'],
                    graph.vs[vertex]['Gloss'],
                    i + 1])
            if verbose:
                print('---')
        for node, data in _graph.nodes(data=True):
            data['infomap'] = D[node]

    args.api.save_graph(_graph, 'infomap', threshold, edgefilter, log=args.log)


@command()
def colexification_dump(args):
    concepticon = load_concepticon(args.api, Concepticon(args.concepticon_repos))
    languages = [line[0] for line in args.api.csv_reader('stats', 'languages')]
    all_colexifications = [
        (l[0], l[1]) for l in args.api.csv_reader('stats', 'colexifications')][1:]
    concepts = set()
    for c1, c2 in all_colexifications:
        concepts.add(c1)
        concepts.add(c2)
    concepts = sorted(concepts)

    with args.api.csv_writer('dumps', 'dump') as writer:
        for i, wl in args.api.iter_wordlists(args.glottolog_repos, args.log):
            if wl['meta']['identifier'] in languages:
                cols = full_colexification(
                    wl,
                    key='Parameter_ID',
                    entry='Clics_Value',
                    indices='identifiers')
                tmp = defaultdict(int)
                for k, v in cols.items():
                    for (conceptA, idxA), (conceptB, idxB) in combinations(v, r=2):
                        tmp[conceptA, conceptB] += 1
                        tmp[conceptB, conceptA] += 1
                    for cnc, idx in v:
                        tmp[cnc, cnc] += 1
                for cnc in concepts:
                    writer.writerow([
                        wl['meta']['family'], wl['meta']['identifier'],
                        '"'+concepticon[cnc]['gloss']+'"',
                        '"'+concepticon[cnc]['gloss']+'"',
                        str(tmp[cnc, cnc])])
                for cidxA, cidxB in all_colexifications:
                    if tmp[cidxA, cidxB] or (tmp[cidxA, cidxA] and tmp[cidxB, cidxB]):
                        writer.writerow([
                            wl['meta']['family'],
                            wl['meta']['identifier'],
                            '"'+concepticon[cidxA]['gloss']+'"',
                            '"'+concepticon[cidxB]['gloss']+'"', str(tmp[cidxA, cidxB])])
                    elif not tmp[cidxA, cidxA] or not tmp[cidxB, cidxB]:
                        writer.writerow([
                            wl['meta']['family'],
                            wl['meta']['identifier'],
                            '"'+concepticon[cidxA]['gloss']+'"',
                            '"'+concepticon[cidxB]['gloss']+'"', '-1'])


@command()
def partialcolexification(args):
    """Function computes partial colexification graph of a given pre-clustered \
            graph.

    Notes
    -----
    Essentially, the search space is restricted in this approach by two
    factors:

    1. only search within the same community for partial colexifications
    2. include the highest cross-community edges.

    """
    cutoff=5
    edgefilter='families'
    verbose=False
    threshold=3
    pairs='infomap'
    graphname='infomap'
    weight='FamilyWeight'
    _tmp = args.api.csv_reader('stats', 'concepts')
    languages = [line[0] for line in args.api.csv_reader('stats', 'languages')]

    concepts = dict([(x[0], dict(zip(_tmp[0], x))) for x in _tmp[1:]])
    G = nx.DiGraph()
    for idx, vals in concepts.items():
        vals['ConcepticonId'] = vals['ID']
        G.add_node(idx, **vals)
    coms = defaultdict(list)
    for a, b, c in args.api.csv_reader('communities', pairs):
        coms[c] += [a]
        G.node[a]['community'] = c
    _graph = args.api.load_graph(graphname, threshold, edgefilter)
    cidx = 1
    for nA, nB, data in _graph.edges(data=True):
        if _graph.node[nA]['infomap'] != _graph.node[nB]['infomap']:
            if data[weight] >= threshold:
                coms[cidx] = [nA, nB]
                cidx += 1

    for i, wl in args.api.iter_wordlists(args.glottolog_repos, args.log):
        pcolnum = 0
        if wl['meta']['identifier'] in languages:
            for com, nodes in coms.items():
                pcols = partial_colexification(
                    wl, nodes,
                    key='Parameter_ID',
                    indices='identifiers',
                    threshold=cutoff,
                    entry='Clics_Value')
                pcolnum += len(pcols)
                for (idxA, wordA, conceptA, idxB, wordB, conceptB) in pcols:
                    if G.edge.get(conceptA, {}).get(conceptB, False):
                        G.edge[conceptA][conceptB]['words'].add((idxA, idxB))
                        G.edge[conceptA][conceptB]['languages'].add(wl['meta']['identifier'])
                        G.edge[conceptA][conceptB]['families'].add(wl['meta']['family'])
                    else:
                        G.add_edge(
                            conceptA,
                            conceptB,
                            words={(idxA, idxB)},
                            languages=set([wl['meta']['identifier']]),
                            families=set([wl['meta']['family']]))
    ignore_edges = []
    with args.api.csv_writer(
            'stats', 'partialcolexifications-{0}-{1}'.format(threshold, edgefilter)) as w:
        for edgeA, edgeB, data in G.edges(data=True):
            data['WordWeight'] = len(data['words'])
            data['words'] = ';'.join(sorted(['{0}/{1}'.format(x, y) for x, y in
                data['words']]))
            data['FamilyWeight'] = len(data['families'])
            data['families'] = ';'.join(sorted(data['families']))
            data['LanguageWeight'] = len(data['languages'])
            data['languages'] = ';'.join(data['languages'])
            if edgefilter == 'families' and data['FamilyWeight'] < threshold:
                ignore_edges += [(edgeA, edgeB)]
            elif edgefilter == 'languages' and data['LanguageWeight'] < threshold:
                ignore_edges += [(edgeA, edgeB)]
            elif edgefilter == 'words' and data['WordWeight'] < threshold:
                ignore_edges += [(edgeA, edgeB)]
            w.writerow([
                edgeA,
                edgeB,
                data['FamilyWeight'],
                data['LanguageWeight'],
                data['WordWeight']])
        G.remove_edges_from(ignore_edges)

    args.api.save_graph(G, 'digraph', threshold, edgefilter, log=args.log)
