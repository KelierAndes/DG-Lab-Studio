"""给发布 zip 生成 `.sha256` 与 `.sig`（发版前跑一次，三个文件一起上传）。

私钥放在 `~/.dgstudio/update_signing_key.pem`，**不进仓库**；应用里只内置公钥
（`dglab/update_key.py`）。签名对象是 `.sha256` 文件本身，所以校验顺序是
「先验签名拿到可信的哈希，再用哈希核对安装包」。

用法：python _tools/sign_update.py dist/DGStudio_vX_win64.zip [...]
依赖：pycryptodome（仅发版机器需要，应用运行时不依赖它）。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

KEY_PATH = Path.home() / ".dgstudio" / "update_signing_key.pem"


def sign(zip_path: Path) -> tuple[Path, Path]:
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15

    key = RSA.import_key(KEY_PATH.read_bytes())
    digest = hashlib.sha256()
    with open(zip_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    manifest = f"{digest.hexdigest()}  {zip_path.name}\n".encode("utf-8")
    sha_path = zip_path.with_name(zip_path.name + ".sha256")
    sig_path = zip_path.with_name(zip_path.name + ".sig")
    sha_path.write_bytes(manifest)
    sig_path.write_bytes(pkcs1_15.new(key).sign(SHA256.new(manifest)))
    return sha_path, sig_path


def main(argv: list[str]) -> int:
    if not argv:
        print("用法：python _tools/sign_update.py <zip> [<zip> ...]")
        return 2
    if not KEY_PATH.is_file():
        print(f"找不到签名私钥：{KEY_PATH}")
        print("先在一台可信机器上生成并离线备份："
              "openssl genrsa -out ~/.dgstudio/update_signing_key.pem 2048")
        return 1
    for raw in argv:
        zip_path = Path(raw)
        if not zip_path.is_file():
            print(f"跳过不存在的文件：{zip_path}")
            continue
        sha_path, sig_path = sign(zip_path)
        print(f"{sha_path.name}\n{sig_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
