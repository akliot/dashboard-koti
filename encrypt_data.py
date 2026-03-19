#!/usr/bin/env python3
"""
Criptografa dados_omie.json e dados_orcamento.json usando AES-256-GCM.
A chave é derivada da senha do dashboard via PBKDF2 (mesmo processo que o browser faz).
Gera arquivos .enc que só podem ser lidos com a senha correta.
"""

import hashlib
import json
import os
import sys
from base64 import b64encode

# Use a mesma senha do dashboard (SHA-256 hash = koti2025)
SENHA = os.environ.get("DASHBOARD_PASSWORD", "koti2025")

def encrypt_file(input_path, output_path):
    """Criptografa um arquivo JSON com AES-256-GCM usando a senha."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
    except ImportError:
        print("  ⚠ cryptography não instalado, instalando...")
        os.system(f"{sys.executable} -m pip install cryptography --quiet")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

    if not os.path.exists(input_path):
        print(f"  ⚠ {input_path} não encontrado, pulando")
        return False

    # Ler dados
    with open(input_path, "r", encoding="utf-8") as f:
        data = f.read()

    # Gerar salt aleatório (16 bytes)
    salt = os.urandom(16)

    # Derivar chave de 256 bits via PBKDF2-SHA256 (100k iterações)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = kdf.derive(SENHA.encode("utf-8"))

    # Criptografar com AES-256-GCM
    nonce = os.urandom(12)  # 96-bit nonce para GCM
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data.encode("utf-8"), None)

    # Salvar como JSON com salt + nonce + ciphertext em base64
    enc_data = {
        "v": 1,  # versão do formato
        "salt": b64encode(salt).decode(),
        "nonce": b64encode(nonce).decode(),
        "data": b64encode(ciphertext).decode(),
        "iter": 100000,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enc_data, f, separators=(",", ":"))

    orig_size = len(data)
    enc_size = os.path.getsize(output_path)
    print(f"  ✅ {os.path.basename(input_path)} → {os.path.basename(output_path)} ({orig_size//1024}KB → {enc_size//1024}KB)")
    return True


def main():
    print("\n🔒 Criptografando dados...", flush=True)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    files = [
        ("dados_omie.json", "dados_omie.enc"),
        ("dados_orcamento.json", "dados_orcamento.enc"),
    ]

    ok = 0
    for src, dst in files:
        src_path = os.path.join(base_dir, src)
        dst_path = os.path.join(base_dir, dst)
        if encrypt_file(src_path, dst_path):
            ok += 1

    print(f"  ✅ {ok} arquivo(s) criptografado(s)")

    # Remover o .js em texto plano (dados_omie.js) — não é mais necessário
    js_path = os.path.join(base_dir, "dados_omie.js")
    if os.path.exists(js_path):
        os.remove(js_path)
        print("  🗑 dados_omie.js removido (substituído por .enc)")


if __name__ == "__main__":
    main()
