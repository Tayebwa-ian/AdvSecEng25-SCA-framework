from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

def aes_encrypt(data: bytes, key: bytes = None) -> dict:
    """
    Encrypts the given data using AES-CBC mode.
    
    Args:
        data (bytes): Data to encrypt.
        key (bytes, optional): 16/24/32-byte AES key. Generates a random key if None.

    Returns:
        dict: A dictionary with 'ciphertext', 'key', and 'iv'.
    """
    if key is None:
        key = get_random_bytes(16)  # 128-bit key
    
    cipher = AES.new(key, AES.MODE_CBC)
    iv = cipher.iv
    ciphertext = cipher.encrypt(pad(data, AES.block_size))
    
    return {
        "ciphertext": ciphertext,
        "key": key,
        "iv": iv
    }

def aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """
    Decrypts AES-CBC encrypted data.

    Args:
        ciphertext (bytes): Encrypted data.
        key (bytes): AES key used during encryption.
        iv (bytes): Initialization vector from encryption.

    Returns:
        bytes: Decrypted plaintext.
    """
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return plaintext
