#!/usr/bin/env python3

import sys, os, re
from IPython import embed
import pandas as pd
from pprint import pprint
import string
from random import shuffle

from LIWC import LIWCutil as liwc
from NRC import NRCutil as nrc
from CONNOTATION import CFutil as conno


def preprocess(doc):
    """Document is a string.
    Tokenizes, removes trailing punctuation from words, counts how many words"""
    better = doc.lower().replace("kind of", "kindof")

    def strip_punct(x):
        if all([c in string.punctuation for c in x]):
            return x
        else:
            return x.strip(string.punctuation)

    toks = [strip_punct(w) for w in better.split()]
    l = len(toks)
    return toks, l


def _extract(lex, toks, n_words, percentage=True, wildcard="*"):
    extracted = {}
    is_weighted = isinstance(list(lex.items())[0][1], dict)

    if wildcard == "":
        wildcard = "~$¬"  # highly unlikely combo

    for w, cats in lex.items():
        w_split = w.split()
        if not any([t.replace(wildcard, "") in " ".join(toks) for t in w_split]):
            continue

        if wildcard in w:
            ngrams = [
                [t.startswith(w_t.replace(wildcard, "")) for t, w_t in zip(tp, w_split)]
                for tp in zip(*[toks[i:] for i in range(len(w_split))])
            ]
            count = sum(map(all, ngrams))
        else:
            ngrams = [list(t) for t in zip(*[toks[i:] for i in range(len(w_split))])]
            count = ngrams.count(w_split)

        if count:
            for c in cats:
                if is_weighted:
                    wg = cats[c]
                else:
                    wg = 1
                extracted[c] = extracted.get(c, 0) + (count * wg)

    if percentage:
        extracted = {k: v / n_words for k, v in extracted.items()}
    return extracted


def extract(lex, doc, percentage=True, wildcard="*"):
    """
    Counts all categories present in the document given the lexicon dictionary.
    percentage (optional) indicates whether to return raw counts or
    normalize by total number of words in the document
    """
    toks, n_words = preprocess(doc)
    return _extract(lex, toks, n_words, percentage, wildcard=wildcard)


def extractVerbsDocs(lex, docs, pct=True):
    nlp = conno.spacy.load("en")
    return [extractVerbs(lex, d, pct=pct, nlp=nlp) for d in docs]


def extractVerbs(lex, doc, pct=True, nlp=None):
    verbs = conno.findVerbs(doc, nlp=nlp)
    n_verbs = len(verbs)
    return _extract(lex, verbs, n_verbs, pct)


def reverse_dict(d):
    cats_to_words = {}
    for w, cs in d.items():
        for c in cs:
            l = cats_to_words.get(c, set())
            l.add(w)
            cats_to_words[c] = l
    return cats_to_words


def sample_cat(rev_d, cat, n=10):
    l = list(rev_d[cat])
    shuffle(l)
    return l[:n]


def main(w):
    if "liwc" in w.lower():
        d = liwc.parse_liwc(w[-4:])
    elif "nrc_emolex" in w.lower():
        d = nrc.parse_emolex()
    elif "nrc_opt" in w.lower():
        d = nrc.parse_opt()
    elif "agency" in w.lower() or "authority" in w.lower():
        d = conno.parse_connotation()
    else:
        print(
            "Unrecognized lexicon name. Available: LIWC2007, LIWC2015, EmoLex, OptPess, Agency, Auth"
        )
        sys.exit(2)

    test = "This is cool. Because, I don't like being social. Abnormally likeable really. Give a man a fish and you feed him for a day; teach a man to fish and you feed him for a lifetime."
    print(preprocess(test))
    from time import time

    start = time()
    d1 = extract(d, test)
    pprint(d1)
    print(time() - start)

    embed()


if __name__ == "__main__":
    which = sys.argv[1]
    main(which)
