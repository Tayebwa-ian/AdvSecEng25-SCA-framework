from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

def aes_encrypt(data: bytes, key: bytes = None) -> dict:
    """
    Encrypts the given data using AES-128 in ECB mode.
    
    Args:
        data (bytes): Data to encrypt.
        key (bytes, optional): 16-byte AES key. Generates a random key if None.

    Returns:
        dict: A dictionary with 'ciphertext' and 'key'.
    """
    if key is None:
        key = get_random_bytes(16)  # 128-bit key

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
        ciphertext (bytes): Encrypted data.
        key (bytes): AES key used during encryption.

    Returns:
        bytes: Decrypted plaintext.
    """
    cipher = AES.new(key, AES.MODE_ECB)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return plaintext
