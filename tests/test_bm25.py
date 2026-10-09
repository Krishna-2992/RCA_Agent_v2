from rca.bm25 import Bm25Index, tokenize
from rca.db import to_sparsevec


def test_identifiers_stay_whole_and_split():
    tokens = tokenize("Error # 984 on IN0011.MST, JCL=A.F8RH0410")
    assert "in0011.mst" in tokens and "in0011" in tokens and "mst" in tokens
    assert "984" in tokens
    assert "f8rh0410" in tokens


def test_light_stemming_on_words_only():
    assert tokenize("printing tickets corrupted files locked") == ["print", "ticket", "corrupt", "file", "lock"]
    assert tokenize("S0C7 U4005") == ["s0c7", "u4005"]


def test_bm25_prefers_rare_matching_terms():
    corpus = [tokenize("LMS file lock IN0011.MST corrupted"),
              tokenize("LMS ticket printing slow"),
              tokenize("LMS login password reset")]
    index = Bm25Index.build(corpus)
    avg = sum(map(len, corpus)) / len(corpus)
    query = index.query_vector("IN0011.MST lock")
    scores = [sum(query.get(i, 0) * w for i, w in index.document_vector(doc, avg).items()) for doc in corpus]
    assert scores[0] > 0 and scores[0] == max(scores)
    assert scores[1] == 0 and scores[2] == 0


def test_sparsevec_text_is_one_based():
    assert to_sparsevec({0: 1.5, 4: 2.0}, 10) == "{1:1.500000,5:2.000000}/10"
