from PackedTrie import *
import sys
from words_alpha import words as words_set #A list of 370_105 words for testing
import unittest

def rep(trie):
    """Exports the trie into some simple lists for debugging"""
    return [list(tier) for tier in trie._TIERS]

words = sorted(words_set)[:1000]

class TrieTest(unittest.TestCase):
    def test_empty_trie_is_empty_non_empty_is_not_empty(self):
        trie = PackedTrie()
        self.assertTrue(trie.is_empty)
        trie.insert("a")
        self.assertFalse(trie.is_empty)

    def test_insert_remove_insert_with_prefix(self):
        trie = PackedTrie()
        for word in words[:100]:
            trie.insert(word)

        trie_copy = trie

        for word in words[:100]:
            trie.remove(word)

        self.assertTrue(trie.is_empty)
        self.assertEqual(trie.with_prefix(""), [])

        for word in words[:100]:
            trie.insert(word)

        self.assertEqual(trie, trie_copy)
        self.assertEqual(trie.with_prefix(""), sorted(words[:100]))

    def test_insert(self):
        trie = PackedTrie()
        for word in words[:100]:
            trie.insert(word)
        
        self.assertEqual(sorted(words[:100]), trie.with_prefix(""))

    def test_unicode_handling(self): # If Trie is changed to not handle full unicode, this will fail
        trie = PackedTrie()
        words = ["café", "naïve", "résumé", "🐍python", "добрый"]
        for word in words:
            trie.insert(word)
        for word in words:
            self.assertIn(word, trie)
        self.assertEqual(sorted(words), trie.with_prefix(""))

    def test_len_and_node_count(self):
        trie = PackedTrie()
        self.assertEqual(len(trie), 0)
        trie.insert("a")
        trie.insert("ab")
        trie.insert("ad")
        self.assertEqual(len(trie), 3)
        self.assertEqual(trie.node_count, 4)  #Root is also a node

    def test_iter(self):
        trie = PackedTrie()
        li = sorted(words[:100])

        for word in li:
            trie.insert(word)

        for i, word in enumerate(trie):
            self.assertEqual(word, li[i])

    def test_clear(self):
        trie1 = PackedTrie()
        for word in words[:100]:
            trie1.insert(word)
        trie2 = PackedTrie()

        self.assertNotEqual(rep(trie1), rep(trie2))
        trie1.clear()
        self.assertEqual(rep(trie1), rep(trie2))

    def test_insert_non_str_raises_bad_removal(self):
        trie = PackedTrie()
        with self.assertRaises(TypeError):
            trie.insert(123)
        with self.assertRaises(TypeError):
            trie.insert(None)
        with self.assertRaises(KeyError):
            trie.remove("foo")

    def test_contains_and_has_prefix(self):
        trie = PackedTrie()
        trie.insert("apple")
        self.assertTrue("apple" in trie)
        self.assertTrue(trie.has_prefix("app"))
        self.assertFalse("app" in trie)

    def test_iteration_and_mutation_detection(self):
        trie = PackedTrie()
        trie.insert("a")
        trie.insert("b")
        it = iter(trie)
        next(it)
        trie.insert("c")
        with self.assertRaises(RuntimeError):
            next(it)

    def test_sizeof_increases_after_insert(self):
        trie = PackedTrie()
        base_size = sys.getsizeof(trie)
        trie.insert("h")
        new_size = sys.getsizeof(trie)
        self.assertGreater(new_size, base_size)

    def test_methods_type_handling(self):
        trie = PackedTrie()
        methods = [trie.insert, trie.remove, trie.with_prefix, trie._search_helper, trie.has_prefix, trie.__contains__]

        for method in methods:
            with self.assertRaises(TypeError):
                method(1)
        
        for method in methods:
            method("foo")

    def test_insert_order_remove(self):
        trie1 = PackedTrie()
        trie2 = PackedTrie()

        trie1.insert("foo")
        trie1.insert("bar")

        trie2.insert("bar")
        trie2.insert("foo")

        self.assertNotEqual(rep(trie1), rep(trie2))
        self.assertEqual(set(trie1), set(trie2))

        trie2.remove("bar")
        trie2.remove("foo")

        trie1.remove("foo")
        trie1.remove("bar")

        self.assertEqual(rep(trie1), rep(trie2))
        self.assertEqual(set(trie1), set(trie2))

    def test_empty_and_very_long_string(self):
        trie = PackedTrie()

        with self.assertRaises(ValueError):
            trie.insert("")

        long_str = "a" * 10_000 #The trie is nonrecursive, no 1000 recursiondepth limit
        trie.insert(long_str)
        self.assertTrue(long_str in trie)
        self.assertTrue(trie.has_prefix("a" * 100))
        self.assertEqual(trie.with_prefix(""), [long_str])
        trie.remove(long_str)
        self.assertFalse(long_str in trie)
        
        rep_li = rep(trie)
        for i, lists in enumerate(rep_li): #Trie is emptied, not just empty
            for n in lists if i > 0 else lists[1:]:
                self.assertEqual(n, (0, 0, 0, 0))

    def test_exceeding_unicode(self):
        trie = PackedTrie()

        max_char = min(1_114_111, 2**trie._TIERS[0]._NODE_FORMAT[0] - 1)

        trie.insert(chr(max_char))
        with self.assertRaises(ValueError):
            invalid_char = chr(max_char + 1)
            trie.insert(invalid_char)

    def test_encoding_based_size(self):
        trie_uni = PackedTrie(encoding="unicode")
        trie_ascii = PackedTrie(encoding="ascii")

        for word in words[:10]:
            trie_uni.insert(word)
            trie_ascii.insert(word)

        self.assertGreater(sys.getsizeof(trie_uni), sys.getsizeof(trie_ascii))

    def test_pack_unpack_roundtrip(self):
        tier = PackedTrie()._TIERS[0]
        p0, p2, p3 = min(2**tier._NODE_FORMAT[0] - 1, 1_114_111), 2**tier._NODE_FORMAT[2] - 1, 2**tier._NODE_FORMAT[3] - 1 # max values for each field
        data = (p0, 1, p2, p3)
        packed = tier.pack_node(*data)
        self.assertEqual(len(packed), tier._NODE_SIZE)
        unpacked = tier.unpack_node(packed)
        self.assertEqual(unpacked, data)

    def test_free_nodes_push_pop(self):
        tier = PackedTrie()._TIERS[0]
        for v in (1, 256, 65536):
            tier._free_nodes_push(v)
        self.assertEqual(tier._free_nodes_pop(), 65536)
        self.assertEqual(tier._free_nodes_pop(), 256)
        self.assertEqual(tier._free_nodes_pop(), 1)
        self.assertEqual(len(tier._free_nodes), 0)

    def test_unicode_range(self):
        trie = PackedTrie(encoding=(98, 121))
        trie.insert("b")
        trie.insert("y")

        with self.assertRaises(ValueError):
            trie.insert("a")
        with self.assertRaises(ValueError):
            trie.insert("z")

    def test_multiple_ranges_insert_and_retrieve(self):
        trie = PackedTrie(encoding=["latin_alphabet", "greek", "cyrillic"])
        
        words = ["abc", "λμνa", "привет"]
        for word in words:
            trie.insert(word)
        
        for word in words:
            self.assertIn(word, trie)
        
        self.assertEqual(set(trie), set(words))

    def test_outside_range_raises(self):
        trie = PackedTrie(encoding=["latin_alphabet", "greek"])
        
        with self.assertRaises(ValueError):
            trie.insert("привет")  # Cyrillic not allowed

    def test_overlapping_ranges_merge_correctly(self):
        trie = PackedTrie(encoding=[(98, 122), (120, 125)])

        with self.assertRaises(ValueError):
            trie.insert("a")
        with self.assertRaises(ValueError):
            trie.insert("~")    #~ is Unicode point 126

        trie.insert("xyz}")     #} is unicode point 125
        self.assertIn("xyz}", trie)
        self.assertEqual(len(trie), 1)

    def test_encoding_decoding_disjointed(self):
        trie = PackedTrie(encoding=[(234, 257), (1337, 1354)])
        li = [i for i in range(257, 234 + 1)] + [i for i in range(1354, 1337 + 1)]

        for i in range(len(li)):
            self.assertEqual(trie._decode_char(i), chr(li[i]))
            self.assertEqual(trie._encode_char(li[i]), i)

    def test_allowed_chars(self):
        trie = PackedTrie(encoding=["ascii", "latin_extended_b", "japanese"])
        for char in trie.allowed_chars():
            trie.insert(char)

if __name__ == '__main__':
    unittest.main()