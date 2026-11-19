"""Presse-papiers fiable sous Windows (programme PC et générateur).

Tk, sous Windows, ne dépose le texte qu'« à la demande » : si le générateur est fermé
avant le collage (cas normal : on ferme le générateur puis on lance l'installation),
le presse-papiers est vide. Ici le texte est déposé tout de suite par l'API Windows
et reste disponible même après la fermeture du programme.
"""
import sys
import time

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _api():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.restype = wintypes.BOOL
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.restype = wintypes.HGLOBAL
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.argtypes = []
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.argtypes = []
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    u32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    return ctypes, k32, u32


def _ouvrir(u32, hwnd) -> bool:
    for _ in range(20):                       # un autre programme peut le tenir un instant
        if u32.OpenClipboard(hwnd):
            return True
        time.sleep(0.05)
    return False


def _hwnd(widget):
    try:
        return int(widget.winfo_toplevel().winfo_id())
    except Exception:
        return None


def _copier_win32(widget, texte: str) -> bool:
    try:
        ctypes, k32, u32 = _api()
        donnees = (texte + "\0").encode("utf-16-le")
        if not _ouvrir(u32, _hwnd(widget)):
            return False
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(donnees))
            if not h:
                return False
            p = k32.GlobalLock(h)
            if not p:
                k32.GlobalFree(h)
                return False
            ctypes.memmove(p, donnees, len(donnees))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(CF_UNICODETEXT, h):
                k32.GlobalFree(h)
                return False
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def _copier_clip(texte: str) -> bool:
    """Secours : clip.exe (présent depuis Windows 7)."""
    try:
        import subprocess
        subprocess.run(["clip"], input=texte.encode("utf-16"), check=True, timeout=5,
                       creationflags=0x08000000)          # CREATE_NO_WINDOW
        return True
    except Exception:
        return False


def _coller_win32() -> str:
    try:
        ctypes, k32, u32 = _api()
        if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        if not _ouvrir(u32, None):                # lecture : pas besoin de fenêtre
            return ""
        try:
            h = u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = k32.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                k32.GlobalUnlock(h)
        finally:
            u32.CloseClipboard()
    except Exception:
        return ""


def coller(widget) -> str:
    """Texte du presse-papiers ('' s'il est vide)."""
    if sys.platform == "win32":
        t = _coller_win32()
        if t:
            return t
    try:
        return str(widget.clipboard_get() or "")
    except Exception:
        return ""


def copier(widget, texte: str) -> bool:
    """Dépose `texte` dans le presse-papiers ; renvoie True si c'est vérifié."""
    texte = str(texte)
    if sys.platform == "win32":
        if _copier_win32(widget, texte) and _coller_win32().strip() == texte.strip():
            return True
        if _copier_clip(texte) and _coller_win32().strip() == texte.strip():
            return True
    try:
        widget.clipboard_clear()
        widget.clipboard_append(texte)
        widget.update()
        return coller(widget).strip() == texte.strip()
    except Exception:
        return False


def menu_clic_droit(champ, coller_seulement: bool = False):
    """Ajoute le menu clic droit Couper / Copier / Coller à une case de saisie Tk."""
    import tkinter as tk
    m = tk.Menu(champ, tearoff=0)
    if not coller_seulement:
        m.add_command(label="Couper", command=lambda: champ.event_generate("<<Cut>>"))
        m.add_command(label="Copier", command=lambda: champ.event_generate("<<Copy>>"))

    def _coller():
        t = coller(champ)
        if not t:
            return
        try:
            champ.delete("sel.first", "sel.last")
        except Exception:
            pass
        champ.insert("insert", t.strip())
    m.add_command(label="Coller", command=_coller)

    def montrer(e):
        champ.focus_set()
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
    champ.bind("<Button-3>", montrer)
    return m
