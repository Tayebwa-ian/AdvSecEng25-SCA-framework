def hw_slow(a:int):
    """Hamming weight of binary representation of a assuming the given number of bits (if a<0 assume 2's complement)."""
    cnt = 0
    while a:
        cnt += a%2
        a = a >> 1
    return cnt

hamming_dict = {i: hw_slow(i) for i in range(256)}

def hw(a:int):
    """Hamming weight of binary representation of a assuming the given number of bits (if a<0 assume 2's complement)."""
    cnt = 0
    while a:
        cnt += hamming_dict[a%256]
        a = a >> 8
    return cnt

def hd(a:int, b:int):
    return hw(a ^ b)