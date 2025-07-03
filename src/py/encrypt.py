from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

def aes_encrypt(data: bytes, key: bytes = None) -> dict:
    """
    Encrypts the given data using AES-128 in ECB mode.
    
    Args:
        data (bytes): Must be exactly 16 bytes (one AES block).
        key (bytes, optional): 16-byte AES key. Generates a random key if None.

    Returns:
        dict: A dictionary with 'ciphertext' and 'key'.
    """
    if key is None:
        key = get_random_bytes(16)

    assert len(data) == 16, "AES ECB requires a 16-byte block"
    cipher = AES.new(key, AES.MODE_ECB)
    ciphertext = cipher.encrypt(data)
    return {
        "ciphertext": ciphertext,
        "key": key
    }

def aes_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """
    Decrypts AES-128 ECB encrypted data.

    Args:
        ciphertext (bytes): Must be exactly 16 bytes.
        key (bytes): 16-byte AES key.

    Returns:
        bytes: Decrypted plaintext.
    """
    assert len(ciphertext) == 16, "AES ECB ciphertext must be 16 bytes"
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.decrypt(ciphertext)
