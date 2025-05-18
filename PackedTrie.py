import sys
assert sys.version_info >= (3, 10), "Requires Python 3.10+"

__all__ = ['packedtrie']

class _TrieTier:
    """Internal memory efficient class storing bytearrays of nodes. Each tier preallocates memory for 2**tier large chunks of nodes.
    Each node in the array stores a offset to a unicode character (ord(char) - self._encoding[0]), a reference to its childrens position, and an end-of-word flag. 

    Format\n
    1 - 21 bits: encoded character\n
    1 bit: flag end-of-word\n
    1 - 5 bits: detailing which tier its children are in\n
    1 - n bits: detailing the position of the children in the tier array\n

    Fills precisely a number of bytes, dependant on encoding and size_class
    """
    def __init__(self, tier: int, TIERS: list, encoding_range, size_class_internal):
        # Each tier is has a bytearray which preallocates memory in chunks, each chunk is of size 2**tier * 6 bytes, able to handle 2**tier children

        self._encoding = encoding_range
        self._size_class_internal = size_class_internal
        
        def smallest(char):
            tier = 1
            while 2**tier < char + 1:
                tier += 1
            return tier
        
        encoding_char = len(bin(encoding_range - 1)[2:])   #Enough bits to encode the unicode length
        encoding_flag = 1   #end-of-word flag
        encoding_tier = smallest(encoding_char) #The smallest number of tiers possible, given the number of characters
        encoding_pos = (8 - (encoding_char + encoding_flag + encoding_tier)) % 8 + 8 + 8*self._size_class_internal  #position index within each tier, dependant on the size_class and padded out

        #Creates the node format, of form (char, flag, tier, pos)
        self._NODE_FORMAT = (encoding_char, encoding_flag, encoding_tier, encoding_pos)

        self._NODE_SIZE = sum(self._NODE_FORMAT) // 8 # Size of a node in bytes
        
        self._SHIFTS = []    #For use in packing and unpacking
        self._MASKS = []
        shift = self._NODE_SIZE * 8

        for bits in self._NODE_FORMAT:
            shift -= bits
            self._SHIFTS.append(shift)
            self._MASKS.append((1 << bits) - 1)
        del shift, bits


        self.arr = bytearray() #The main array for the tier
        self._free_nodes = bytearray() #Stores an array of free nodes, push and pop with _free_nodes_pop() and _free_nodes_push()
        self._free_nodes_arr_size_per_pos = int(encoding_pos / 8) if encoding_pos / 8 == int(encoding_pos / 8) else int(encoding_pos / 8) + 1
        self.tier = tier
        self.TIERS = TIERS # TIERS is a list of _TrieTiers

        self._EMPTY_NODE = bytearray(self._NODE_SIZE) # An empty node to be used for reference
        self._SENTINEL_TIER = 2**(self._NODE_FORMAT[2]) - 1 # Used as a compact sentinel

    def _index_to_bytepos(self, index):
        return index * (2 ** self.tier) * self._NODE_SIZE

    def _bytepos_to_index(self, bytepos):
        return bytepos // ((2 ** self.tier) * self._NODE_SIZE)

    def _find_pos(self, char, pos):
        """Finds the position of a child using binary or linear search, returns the position of the first byte with the child, relative to the whole array"""
        lo = self._index_to_bytepos(pos)
        arr = memoryview(self.arr)
        size = self._NODE_SIZE

        hi = lo + 2 ** self.tier * size

        if self.tier <= 2:
            while lo < hi:
                if arr[lo:lo+size] == self._EMPTY_NODE:
                    return False, lo
                ch = self._unpack_char(arr[lo:lo+size])
                if ch >= char:
                    return ch == char, lo
                lo += size

        while lo < hi:
            mid = ((lo + hi) // 2) // size * size
            if arr[mid:mid+size] == self._EMPTY_NODE:
                hi = mid
                continue
            ch = self._unpack_char(arr[mid:mid+size])
            if char == ch:
                return True, mid 
            elif char < ch:
                hi = mid
            else:
                lo = mid + size
        return False, lo
    
    def _add_children(self, children):
        """Adds a children chunk. Returns its index position."""
        chunk_size = (2 ** self.tier) * self._NODE_SIZE

        if self._free_nodes:
            pos = self._free_nodes_pop()
            bytepos = self._index_to_bytepos(pos)

            self.arr[bytepos : bytepos + chunk_size] = self._EMPTY_NODE * (2 ** self.tier)

            self.arr[bytepos: bytepos + len(children)] = children

        else:
            if len(self) >= 2**(self._NODE_FORMAT[3]) - 1:
                size_classes = ("tiny", "default", "large", "massive", "massive+", "massive++", "massive+++", "")
                raise OverflowError(f"Trie tier {self.tier} exceeds reference limit ({2**(self._NODE_FORMAT[3]) -1} chunks)\n   Consider calling .rebuild(size_class='{(size_classes)[self._size_class_internal + 1]}')")

            pos = len(self.arr) // chunk_size
            self.arr.extend(children)
            assert len(children) <= chunk_size, f"len(children)={len(children)}, chunk_size={chunk_size}, tier={self.tier}"
            self.arr.extend(bytearray(chunk_size - len(children)))

        return pos

    def _add_node(self, pos, node_key, flag):
        """Adds a node to a children chunk, moves following nodes over to maintain sorted order, moves the chunk up or down a tier when necessary. 
        Returns the index position of all children to handle if its moved a tier"""
        base = self._index_to_bytepos(pos)
        val, node_pos = self._find_pos(node_key, pos)
        node_idx = node_pos // self._NODE_SIZE

        if val:
            char, old_flag, tier, child_pos = self.unpack_node(self.arr[node_pos: node_pos + self._NODE_SIZE])
            if flag and not old_flag:
                self.arr[node_pos: node_pos + self._NODE_SIZE] = self.pack_node(char, 1, tier, child_pos)

            return False, flag and not old_flag, self.tier, node_idx, pos

        if self.arr[base + 2 ** self.tier * self._NODE_SIZE - self._NODE_SIZE: base + 2 ** self.tier * self._NODE_SIZE] != self._EMPTY_NODE:
            pos = self._move_tier_up(pos)
            return self.TIERS[self.tier + 1]._add_node(pos, node_key, flag)
        elif self.tier > 2 and self.arr[base + 2 ** self.tier: base + 2 ** self.tier + self._NODE_SIZE] == self._EMPTY_NODE: #Moves a node down when it is 1/3rd full
            pos = self._move_tier_down(pos)
            return self.TIERS[self.tier - 1]._add_node(pos, node_key, flag)

        self.arr[node_pos + self._NODE_SIZE: base + 2 ** self.tier * self._NODE_SIZE] = self.arr[node_pos: base + 2 ** self.tier * self._NODE_SIZE - self._NODE_SIZE]
        child_pos = 0
        self.arr[node_pos: node_pos + self._NODE_SIZE] = self.pack_node(node_key, flag, self._SENTINEL_TIER, child_pos)

        return True, flag, self.tier, node_idx, pos

    def _remove_children(self, pos):
        """Empties a children chunk"""
        byte_offset = self._index_to_bytepos(pos)
        self._free_nodes_push(pos)
        res = self.arr[byte_offset: byte_offset + (2 ** self.tier) * self._NODE_SIZE]
        self.arr[byte_offset: byte_offset + (2 ** self.tier) * self._NODE_SIZE] = self._EMPTY_NODE * (2 ** self.tier)
        return res

    def _remove_node(self, pos, char):
        """Removes a node from a children chunk, moves following nodes over to maintain sorted order. Returns the bool of whether the node was found"""
        base = self._index_to_bytepos(pos)
        found, offset = self._find_pos(char, pos)
        if not found:
            return False

        end = base + (2 ** self.tier) * self._NODE_SIZE
        self.arr[offset:end - self._NODE_SIZE] = self.arr[offset + self._NODE_SIZE:end]
        self.arr[end - self._NODE_SIZE:end] = self._EMPTY_NODE
        return base // self._NODE_SIZE #The index of the first child, to be passed for __getitem__ in abort child

    def _move_tier_up(self, pos):
        node = self._remove_children(pos)
        if len(self.TIERS) <= self.tier + 1:
            self.TIERS.append(_TrieTier(self.tier + 1, self.TIERS, self._encoding, self._size_class_internal))
        return self.TIERS[self.tier + 1]._add_children(node)

    def _move_tier_down(self, pos): # Never call if the current tier is 0
        node = self._remove_children(pos)[:(2 ** (self.tier - 1)) * self._NODE_SIZE]
        return self.TIERS[self.tier - 1]._add_children(node)
    
    def _give_children(self, node_idx, new_char, new_flag):
        """Adds a child to a node, if the child chunk is moved, changes the tier index and positional index of the parent node"""
        char, flag, tier, child_pos = self[node_idx]

        if tier == self._SENTINEL_TIER: 
            tier = 0
            child_pos = self.TIERS[0]._add_children(bytearray(self._NODE_SIZE))

        val1, val2, newtier, newpos, pos  = self.TIERS[tier]._add_node(child_pos, new_char, new_flag)

        self.arr[node_idx * self._NODE_SIZE : node_idx * self._NODE_SIZE + self._NODE_SIZE] = self.pack_node(char, flag, newtier, pos)

        return val1, val2, newtier, newpos

    def _free_nodes_push(self, val):
        """Stores a position in the array"""
        self._free_nodes.extend(val.to_bytes(self._free_nodes_arr_size_per_pos, byteorder="big"))

    def _free_nodes_pop(self):
        """Pops a position from the array"""
        val = int.from_bytes(self._free_nodes[-self._free_nodes_arr_size_per_pos:], byteorder="big")
        del self._free_nodes[-self._free_nodes_arr_size_per_pos:]
        return val
    
    def pack_node(self, *data):
        val = 0
        for i in range(len(data)):
            val |= data[i] << self._SHIFTS[i]
        return val.to_bytes(self._NODE_SIZE, "big")

    def unpack_node(self, data):
        val = int.from_bytes(data, "big")
        return tuple((val >> self._SHIFTS[i]) & self._MASKS[i] for i in range(len(self._NODE_FORMAT)))

    def _unpack_char(self, data): # For faster unpacking when searching, assumes the first bits are a char
        val = int.from_bytes(data, "big")
        return (val >> self._SHIFTS[0]) & self._MASKS[0]
    
    def __getitem__(self, idx):
        start = idx * self._NODE_SIZE
        end = start + self._NODE_SIZE
        if end > len(self.arr):
            raise IndexError("Index out of range")
        return self.unpack_node(self.arr[start:end])

    def __len__(self):
        return len(self.arr) // self._NODE_SIZE
    
    def __sizeof__(self):
        size = object.__sizeof__(self)
        size += sys.getsizeof(self.arr)
        size += sys.getsizeof(self._free_nodes)
        size += sys.getsizeof(self._SHIFTS)
        size += sys.getsizeof(self._MASKS)
        return size

class PackedTrie:
    """Create a new PackedTrie type object

    PackedTrie(*, encoding="unicode", size_class="default")"""

    def __init__(self, *, encoding: str | tuple | set[str | int] | list[str | tuple | set[str | int]]= "unicode", size_class: str ="default"):
        if size_class not in ("tiny", "default", "large", "massive", "massive+", "massive++"):
            raise ValueError("Size must be one of: tiny, default, large, massive, massive+, massive++")
        if not encoding:
            raise ValueError("Encoding must not be empty")
        
        self._encoding_range, self._encoding = self._verify_encoding(encoding)  #This is quite heavy on overhead, but is constant
        
        self._size_class = size_class
        self._TIERS = [] #All nodes will be stored here, while the nodes themselves store indices to their children
        self._TIERS.append(_TrieTier(0, self._TIERS, self._encoding_range, ["tiny", "default", "large", "massive", "massive+", "massive++"].index(size_class)))
        self._TIERS[0].arr.extend(self._TIERS[0].pack_node(0, 0, self._TIERS[0]._SENTINEL_TIER, 0)) #root is self._TIERS[0][0] 
        self._node_count = 1
        self._word_count = 0
        self.version = 0 #To be updated during runtime, ensures __iter__ cannot be silently corrupted

    def _verify_encoding(self, _encoding) -> tuple:
        """Verifies each inserted element and returns a tuple of all ranges of unicode to be used"""
        if type(_encoding) != list: _encoding = [_encoding]
        encoding = []
        for elem in _encoding:
            encoding += self._verify_encoding_item(elem)

        return self._merge_ranges(encoding)
        
    def _verify_encoding_item(self, encoding):
        """Returns the desired unicode range"""

        _ranges = {
            "ascii": [(0, 127)],
            "printable_ascii": [(32, 126)],
            "digits": [(48, 57)],
            "punctuation": [(33, 47), (58, 64), (91, 96), (123, 126)],
            "alphanumeric": [(48, 57), (65, 90), (97, 122)],

            "latin_alphabet": [(65, 90), (97, 122)],
            "latin_alphabet_lowercase": [(97, 122)],
            "latin_alphabet_uppercase": [(65, 90)],
            "latin": [(0, 383)],
            "latin_extended_a": [(384, 591)],
            "latin_extended_b": [(592, 687)],

            "greek": [(880, 1023)],
            "cyrillic": [(1024, 1279)],
            "hebrew": [(1424, 1535)],
            "arabic": [(1536, 1791)],
            "devanagari": [(2304, 2431)],
            "thai": [(3584, 3711)],

            "japanese": [(12352, 12543), (12592, 12687)],
            "korean": [(44032, 55215)],
            "hangul_compat": [(12593, 12687)],
            "cjk": [(19968, 40959)],

            "symbols_math": [(8704, 8959)],
            "currency_symbols": [(8352, 8399)],
            "arrows": [(8592, 8703)],
            "misc_technical": [(8960, 9215)],

            "emojis_basic": [(128512, 128591)],
            "emojis_extended": [(127744, 128591), (129296, 129535)],

            "fullwidth_forms": [(65281, 65374)],
            "bmp": [(0, 65535)],
            "unicode": [(0, 0x10FFFF)],
        }

        if type(encoding) == str and encoding in _ranges: return _ranges[encoding]
        if type(encoding) == tuple and len(encoding) == 2 and 0 <= encoding[0] <= encoding[1] <= 0x10FFFF: return [encoding]
        if type(encoding) == set and all(type(ch) == str and len(ch) == 1 for ch in encoding): return [(ord(char), ord(char)) for char in encoding]
        if type(encoding) == set and all(type(ch) == int and 0 <= ch <= 0x10FFFF for ch in encoding): return [(char, char) for char in encoding]

        raise ValueError(f"Encoding must be one of:"
                        "\n  A named range like 'unicode' or 'ascii' (see .help() for full list)"
                        "\n  A set of characters eg {'a', 'c'}"
                        "\n  A set of unicode codepoints, eg {97, 99} for 'a', 'b'"
                        "\n  A tuple defining a range (start, end)"
                        "\nOr list of any combination of the above")

    @staticmethod
    def _merge_ranges(encoding) -> tuple[int, tuple]:
        """Merges a list of tuple ranges into as few tuples as possible"""
        encoding.sort()
        chr_range = 0
        idx = 1

        while idx < len(encoding):
            if encoding[idx - 1][1] + 1 >= encoding[idx][0]:
                encoding[idx - 1] = (encoding[idx - 1][0], max(encoding[idx - 1][1], encoding[idx][1]))
                encoding.pop(idx)
            else:
                chr_range += encoding[idx - 1][1] - encoding[idx - 1][0] + 1
                idx += 1

        chr_range += encoding[-1][1] - encoding[-1][0] + 1
        return chr_range, tuple(encoding)

    def _encode_char(self, char) -> int:
        encoding = self._encoding
        ch_ord = ord(char)
        offset = 0
        for start, end in encoding:
            if start <= ch_ord <= end:
                return offset + (ch_ord - start)
            offset += end - start + 1
        raise ValueError(f"Character '{char}' outside allowed unicode code points:\n" +
                        "\n".join(f"U+{r[0]:04X} -> U+{r[1]:04X}" for r in encoding))

    def _decode_char(self, num) -> str:
        encoding = self._encoding
        offset = 0

        for start, end in encoding:
            length = end - start + 1
            if offset <= num < offset + length:
                return chr(start + num - offset)
            offset += length
            
        raise ValueError(f"Decoded value {num} outside allowed index range, max point {self._encoding_range}")

    def insert(self, string: str) -> None:
        """Inserts a string to the Trie"""
        if type(string) != str:
            raise TypeError("Expected a string")
        if not string:
            raise ValueError("String cannot be empty")
        
        string_li = [self._encode_char(ch) for ch in string]
        
        tier, pos = 0, 0
        for i, char in enumerate(string_li):
            try:
                node_val, end_val, tier, pos = self._TIERS[tier]._give_children(pos, char, 1 if i == len(string) - 1 else 0)
            except OverflowError as e:
                if i == 0 or string[:i - 1] in self:
                    raise OverflowError(f"{e}\nString '{string}' not inserted") from e
                self.insert(string[:i - 1])
                self.remove(string[:i - 1])
                raise OverflowError(f"{e}\nString '{string}' not inserted") from e
            if node_val: self._node_count += 1
        if end_val: self._word_count += 1

        self.version += 1
        
    def remove(self, string: str) -> None:
        """Removes a string from the Trie"""
        if type(string) != str:
            raise TypeError("Expected a string")
        if self.is_empty:
            raise KeyError("The string you're trying to remove doesn't exist; Trie is empty")

        node = self._TIERS[0][0] #(char, flag, tier, pos)
        stack = []

        for char in string:
            found, byte_pos = self._TIERS[node[2]]._find_pos(self._encode_char(char), node[3])
            if not found:
                raise KeyError("The string you're trying to remove doesn't exist")
            node_idx = byte_pos // self._TIERS[node[2]]._NODE_SIZE
            newnode = self._TIERS[node[2]][node_idx]
            stack.append((node[2], node[3], node_idx, newnode))
            node = newnode

        tier, pos, node_idx, node = stack[-1]

        if not node[1]:
            raise KeyError("The string you're trying to remove doesn't exist")

        self._TIERS[tier].arr[node_idx * self._TIERS[tier]._NODE_SIZE : (node_idx + 1) * self._TIERS[tier]._NODE_SIZE] = self._TIERS[tier].pack_node(node[0], 0, node[2], node[3])

        self._word_count -= 1
        self.version += 1

        if node[2] != self._TIERS[0]._SENTINEL_TIER:
            return
                
        for i in range(len(stack) - 1, -1, -1):
            tier, pos, node_idx, node = stack[i]
            _, flag, _, _ = self._TIERS[tier][node_idx]
            if flag:
                 break

            node_idx = self._TIERS[tier]._remove_node(pos, node[0])
            self._node_count -= 1

            byte_pos = self._TIERS[tier]._index_to_bytepos(pos)
            if self._TIERS[tier].arr[byte_pos : byte_pos + self._TIERS[tier]._NODE_SIZE] != self._TIERS[tier]._EMPTY_NODE:
                return

            self._TIERS[tier]._remove_children(pos)

            if i > 0:
                ptier, ppos, pidx, pnode = stack[i - 1]
                self._TIERS[ptier].arr[pidx * self._TIERS[ptier]._NODE_SIZE : (pidx + 1) * self._TIERS[ptier]._NODE_SIZE] = \
                    self._TIERS[ptier].pack_node(pnode[0], pnode[1], self._TIERS[0]._SENTINEL_TIER, 0)
            else:
                self._TIERS[0].arr[0 : self._TIERS[0]._NODE_SIZE] = self._TIERS[0].pack_node(0, 0, self._TIERS[0]._SENTINEL_TIER, 0)

    def clear(self) -> object:
        """Clears the Trie"""
        self._TIERS = []
        self._TIERS.append(_TrieTier(0, self._TIERS, self._encoding_range, ["tiny", "default", "large", "massive", "massive+", "massive++"].index(self._size_class)))
        self._TIERS[0].arr.extend(self._TIERS[0].pack_node(0, 0, self._TIERS[0]._SENTINEL_TIER, 0))
        self._node_count = 1
        self._word_count = 0
        self.version += 1

        return self

    def rebuild(self, *, encoding: str, size_class: str) -> object:
        """Rebuilds the trie, allows you to use a new encoding and/or size_class"""
        if not encoding: encoding = self._encoding
        if not size_class: size_class = self._size_class
        if size_class not in ("tiny", "default", "large", "massive", "massive+", "massive++"):
            raise ValueError("Size must be one of: tiny, default, large, massive, massive+, massive++")    
        
        self._encoding = self._verify_encoding(encoding)
    
        new_trie = PackedTrie(encoding=self._encoding, size_class=["tiny", "default", "large", "massive", "massive+", "massive++"].index(size_class))

        for string in self:
            new_trie.insert(string)

        self._TIERS = new_trie._TIERS
        self.version += 1
        self._size_class = new_trie._size_class
        self._node_count = new_trie._node_count
        self._word_count = new_trie._word_count

        return self

    def with_prefix(self, prefix: str) -> list[str]:
        """Returns a list of all strings in the Trie with the matching prefix. Sorted in unicode order"""
        if type(prefix) != str:
            raise TypeError("Expected a string")
        if prefix == "":
            val = True
            char, flag, tier, pos = self._TIERS[0][0]
        else:
            val, char, flag, tier, pos = self._search_helper(prefix)[0]
        node = (char, flag, tier, pos)

        if not val: return []

        res = []
        stack = [(node, prefix)]

        while stack:
            node, path = stack.pop()
            if node[1]:
                res.append(path)
            if node[2] == self._TIERS[0]._SENTINEL_TIER:
                continue
            for n in range(node[3]*(2**node[2]) + 2**node[2] - 1, node[3]*(2**node[2]) - 1, -1): # The chunk is sorted A->Z , appending it reverses that, so we traverse the chunk in reverse order
                newnode = self._TIERS[node[2]][n]
                if newnode != (0, 0, 0, 0):
                    stack.append((newnode, path + self._decode_char(newnode[0])))
        
        return res

    def _search_helper(self, prefix: str, *, start_dist: int=0, lev_dist: int= 0) -> tuple[bool, int, int, int, int]:
        """Returns the a bool of if the whole prefix exists and the deepest node in a prefix"""
        if type(prefix) != str:
            raise TypeError("Expected a string")
        tier, pos = 0, 0
        _, _, tier, pos = self._TIERS[tier][pos]

        ch = flag = 0 #If search fails in the first value they must be defined

        if self.is_empty:
            return [(False, ch, flag, tier, pos)]

        for i, char in enumerate(prefix):
            val, node_pos = self._TIERS[tier]._find_pos(self._encode_char(char), pos)
            if not val:
                return [(False, ch, flag, tier, pos)]
            ch, flag, tier, pos = self._TIERS[tier][node_pos // self._TIERS[tier]._NODE_SIZE]
            if i == len(prefix) - 1:
                return [(True, ch, flag, tier, pos)]
            elif tier == self._TIERS[0]._SENTINEL_TIER:
                return [(False, ch, flag, tier, pos)]

    def has_prefix(self, prefix: str) -> bool:
        """Returns a bool of whether the Trie has the prefix"""
        if type(prefix) != str:
            raise TypeError("Expected a string")
        return not self.is_empty and self._search_helper(prefix)[0][0]

    @property
    def is_empty(self) -> bool:
        """Returns True if the Trie is empty else False"""
        _, _, tier, pos = self._TIERS[0][0]
        return tier == self._TIERS[0]._SENTINEL_TIER or self._TIERS[tier][pos * 2**tier] == (0, 0, 0, 0)

    @property
    def node_count(self) -> int:
        """Returns the number of nodes in the Trie"""
        return self._node_count

    def __len__(self) -> int:
        """Returns the number of words in the Trie"""
        return self._word_count

    def __sizeof__(self) -> int:
        """Returns the approximate size of the Trie in bytes. Includes internal size."""
        size = object.__sizeof__(self)
        size += sys.getsizeof(self._TIERS)
        size += sys.getsizeof(self._encoding)
        size += sys.getsizeof(self._size_class)
        size += sys.getsizeof(self._encoding_range)
        size += sys.getsizeof(self._node_count)
        size += sys.getsizeof(self._word_count)
        size += sys.getsizeof(self.version)

        size += sum(sys.getsizeof(tier) for tier in self._TIERS)

        return size
    
    def __contains__(self, string: str) -> bool:
        """Returns True if string is in Trie, else False"""
        if type(string) != str:
            raise TypeError("Expected a string")
        if self.is_empty:
            return False
        res = self._search_helper(string)[0]
        return res[0] and res[2]
    
    def __iter__(self):
        """Iterates over all strings in the Trie, yields each string in unicode order"""
        stack = [(self._TIERS[0][0], [])]
        iterver = self.version

        while stack:
            if iterver != self.version:
                raise RuntimeError("Trie mutated during iterations")
            node, path = stack.pop()
            if node[1]:
                yield "".join(path)
            if node[2] == self._TIERS[0]._SENTINEL_TIER:
                continue
            for n in range(node[3]*(2**node[2]) + 2**node[2] - 1, node[3]*(2**node[2]) - 1, -1):
                newnode = self._TIERS[node[2]][n]
                if newnode != (0, 0, 0, 0):
                    stack.append((newnode, path + [self._decode_char(newnode[0])]))
    
    def memory_efficiency(self) -> tuple[int, float, int]:
        """Returns a tuple expressing the approximate memory usage: 
        tuple[0] the size of the trie in bytes; 
        tuple[1] the fraction of memory that is raw node data (closer to 1= better); 
        tuple[2] the amount of overhead in bytes"""
        byte_count = sys.getsizeof(self)
        return byte_count, self._TIERS[0]._NODE_SIZE * self._node_count / byte_count, byte_count - self._TIERS[0]._NODE_SIZE * self._node_count
    
    def __repr__(self) -> str:
        """Returns an abriged string representation of the trie"""
        return f"Trie(words={len(self)}, nodes={self.node_count}, version={self.version}, bytes_per_node_constant={self._TIERS[0]._NODE_SIZE}, " + \
            f"node_format={self._TIERS[0]._NODE_FORMAT}, size_class={self._size_class}, unicode_range=" + \
            "\n"*bool(len(self._encoding) != 1) + " +\n".join(f"U+{r[0]:04X} -> U+{r[1]:04X}" for r in self._encoding) +")"
    
    def allowed_chars(self) -> list:
        """Returns a list of all characters allowed in the trie"""
        res = []
        for i in range(self._encoding_range):
            res.append(self._decode_char(i))
        return res
    
    def __eq__(self, other):
        """Compares the trie against another instance, returns True if both are equal"""
        if not type(other) == PackedTrie:
            return NotImplemented
        if len(other) != len(self) or self.node_count != other.node_count:
            return False
        self_it = iter(self)
        other_it = iter(other)
        try:
            while True:
                if next(self_it) != next(other_it):
                    return False
        except StopIteration:
            return True
        
    @staticmethod
    def help():
        """Print information about how to use the PackedTrie class."""
        print("""PackedTrie()
              
    Constructor: PackedTrie(*, encoding='unicode', size_class='default')

    Encoding options:
    - Named ranges (str):
    General:             'bmp', 'unicode'
    ASCII and subsets    'ascii', 'printable_ascii', 'digits', 'punctuation', 'alphanumeric'
    Symbols:             'symbols_math', 'currency_symbols', 'arrows', 'misc_technical'
    Latin script:        'latin_alphabet', 'latin_alphabet_lowercase', 'latin_alphabet_uppercase',
                    'latin', 'latin_extended_a', 'latin_extended_b'
    Other scripts:       'greek', 'cyrillic', 'hebrew', 'arabic', 'devanagari', 'thai',
                    'japanese', 'korean', 'hangul_compat', 'cjk'
    Special formats:     'emojis_basic', 'emojis_extended', 'fullwidth_forms'

    - tuple:               (start, end) inclusive Unicode range
    - set[str]:            passes a set of allowed chars
    - set[int]             passes a set of allowed unicode points
    - list:                allows combining any of the above
              
        Note: Disjointed ranges are often inefficient in memory, as each range uses a tuple. 
              Consider joining reanges

    Size class options (size_class):
    - 'tiny', 'default', 'large', 'massive', 'massive+', 'massive++'

    Core methods:
    - insert(str)         Add a string
    - remove(str)         Remove a string
    - with_prefix(str)    List strings with a prefix using DFS
    - clear()             Remove all entries
    - rebuild(encoding, size_class)  Rebuild with new parameters

    Properties:
    - is_empty            True if trie is empty
    - node_count          Number of internal nodes
    - len(trie)           Count of stored strings
    - str in trie         Membership check using DFS
    - list(trie)          Iterate all stored strings using DFS
    - repr(trie)          Print summary info
    - memory_efficiency() Byte usage and overhead stats
    - allowed_chars()     A list of allowed characters""")