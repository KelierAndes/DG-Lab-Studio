import ctypes
import os
import shutil
import sys

BASE = r"D:\DG-LAB-X-VRChat-OSC-development\dist\DGStudio\modules\sound_link"
DEPS = os.path.join(BASE, "_deps")
DLL = os.path.join(DEPS, "numpy.libs",
                   "libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll")


def attempt(label, fn):
    try:
        result = fn()
        print(f"  {label}: 成功")
        return result
    except OSError as exc:
        print(f"  {label}: 失败 {exc.winerror} {exc.strerror}")
        return None


def main():
    lib = ctypes.WinDLL(DLL)
    print(f"已映射映像锁: {os.path.basename(DLL)}")

    def rmtree():
        return shutil.rmtree(BASE)

    def rename_dir():
        os.rename(BASE, BASE + ".t")
        os.rename(BASE + ".t", BASE)
        return True

    def rename_file_inplace():
        dst = DLL + ".t"
        os.rename(DLL, dst)
        os.rename(dst, DLL)
        return True

    print("rmtree 整树删除:")
    attempt("rmtree", rmtree)
    if not os.path.isdir(BASE):
        print("  树已被删除,重建空场景退出")
        return
    print("整目录改名:")
    attempt("rename dir", rename_dir)
    print("锁定文件原位改名:")
    attempt("rename file in-place", rename_file_inplace)
    print("锁定文件跨目录改名:")
    os.makedirs(BASE + ".t", exist_ok=True)
    moved = attempt(
        "cross-dir rename",
        lambda: shutil.move(DLL, os.path.join(BASE + ".t", "moved.dll")))
    if moved:
        shutil.move(os.path.join(BASE + ".t", "moved.dll"), DLL)
    shutil.rmtree(BASE + ".t", ignore_errors=True)
    del lib
    print("done")


if __name__ == "__main__":
    main()
