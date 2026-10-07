"""扩展探针:确定哪类句柄会阻止整目录改名/单文件改名。

场景 A: 普通 open() 句柄(无 FILE_SHARE_DELETE)
场景 B: CreateFileMapping 数据映射
场景 C: 子目录句柄
场景 D: 普通 open() 句柄下单文件原位改名
对照: 映像锁(前一轮已测,整目录改名成功)
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil

BASE = r"D:\DG-LAB-X-VRChat-OSC-development\dist\DGStudio\modules\sound_link"
DLL = os.path.join(BASE, "_deps", "numpy.libs",
                   "libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll")
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE = -1


def attempt(label, fn):
    try:
        fn()
        print(f"  {label}: 成功")
        return True
    except OSError as exc:
        print(f"  {label}: 失败 winerror={exc.winerror} ({exc.strerror})")
        return False


def make_handle(path, flags):
    h = ctypes.windll.kernel32.CreateFileW(path, GENERIC_READ, 0, None,
                                           OPEN_EXISTING, flags, None)
    assert h != INVALID_HANDLE, ctypes.GetLastError()
    return h


def scenario(name, holder, close):
    print(f"场景 {name}:")
    handle = holder()
    try:
        attempt("整目录改名", lambda: (os.rename(BASE, BASE + ".t"),
                                        os.rename(BASE + ".t", BASE)))
        attempt("占用文件原位改名",
                lambda: (os.rename(DLL, DLL + ".t"), os.rename(DLL + ".t", DLL)))
    finally:
        close(handle)


def main():
    os.makedirs(os.path.join(BASE, "probe_dir"), exist_ok=True)
    probe_txt = os.path.join(BASE, "probe_dir", "a.txt")
    open(probe_txt, "w").close()

    def plain_open():
        return open(probe_txt, "r")

    scenario("A 普通文件句柄", plain_open, lambda f: f.close())

    def data_map():
        h = make_handle(DLL, 0)
        mapping = ctypes.windll.kernel32.CreateFileMappingW(
            h, None, 0x02, 0, 0, None)  # PAGE_READONLY
        assert mapping
        return (h, mapping)

    def close_map(t):
        ctypes.windll.kernel32.CloseHandle(t[1])
        ctypes.windll.kernel32.CloseHandle(t[0])

    scenario("B 数据映射句柄", data_map, close_map)

    def dir_handle():
        return make_handle(os.path.join(BASE, "probe_dir"),
                           FILE_FLAG_BACKUP_SEMANTICS)

    scenario("C 子目录句柄", dir_handle,
             lambda h: ctypes.windll.kernel32.CloseHandle(h))
    os.remove(probe_txt)
    os.rmdir(os.path.join(BASE, "probe_dir"))
    print("done")


if __name__ == "__main__":
    main()
