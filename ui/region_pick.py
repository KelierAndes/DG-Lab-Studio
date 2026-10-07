
from __future__ import annotations

import io

from PIL import ImageGrab
from win32more import asyncui
from win32more.Microsoft.UI.Xaml import (HorizontalAlignment, Thickness,
                                         VerticalAlignment, Visibility, Window)
from win32more.Microsoft.UI.Xaml.Controls import Grid, Image, TextBlock
from win32more.Microsoft.UI.Xaml.Media import SolidColorBrush, Stretch
from win32more.Microsoft.UI.Xaml.Media.Imaging import BitmapImage
from win32more.Microsoft.UI.Xaml.Shapes import Rectangle
from win32more.Microsoft.UI.Windowing import (DisplayArea,
                                              DisplayAreaFallback,
                                              OverlappedPresenter)
from win32more.Windows.Graphics import PointInt32, SizeInt32
from win32more.Windows.Storage.Streams import (DataWriter,
                                               InMemoryRandomAccessStream)
from win32more.Windows.UI import Color

_MIN_SHOT_PX = 3
_SCALES = 0.94
_active: "_PickerWindow | None" = None
_last_error: str = ""


def pick_region(on_done) -> None:
    _start(on_done, crop_mode=False)


def pick_crop(on_done) -> None:
    _start(on_done, crop_mode=True)


def _start(on_done, crop_mode: bool) -> None:
    global _last_error
    try:
        shot = ImageGrab.grab(all_screens=True)
    except Exception as exc:
        _last_error = f"grab: {exc!r}"
        on_done(None)
        return
    try:
        win = _PickerWindow(shot, crop_mode, on_done)
    except Exception as exc:
        _last_error = f"window: {exc!r}"
        on_done(None)
        return
    _last_error = ""
    win.activate()


class _PickerWindow(Window):
    def __init__(self, shot, crop_mode: bool, on_done):
        super().__init__()
        global _active
        if _active is not None:
            try:
                _active.Close()
            except Exception:
                pass
        _active = self
        self._shot = shot
        self._crop_mode = crop_mode
        self._on_done = on_done
        self._done = False
        self._press = None
        self._f = 1.0
        buf = io.BytesIO()
        shot.save(buf, "PNG")
        self._png = buf.getvalue()
        sw, sh = shot.size
        self._shot_size = (sw, sh)

        self.AppWindow.Title = "截图选取：拖拽框选，松开即确认"
        try:
            presenter = self.AppWindow.Presenter
            if isinstance(presenter, OverlappedPresenter):
                presenter.IsAlwaysOnTop = True
        except Exception:
            pass
        self._wa = DisplayArea.GetFromWindowId(
            self.AppWindow.Id, DisplayAreaFallback.Nearest).WorkArea
        self.AppWindow.Move(PointInt32(self._wa.X, self._wa.Y))
        self.AppWindow.Resize(SizeInt32(self._wa.Width, self._wa.Height))

        self._img = Image()
        self._img.Stretch = Stretch.Fill
        self._rect_el = Rectangle()
        self._rect_el.Stroke = _brush(255, 255, 208, 0)
        self._rect_el.StrokeThickness = 2
        self._rect_el.Fill = _brush(70, 255, 210, 0)
        self._rect_el.Visibility = Visibility.Collapsed

        self._frame = Grid()
        self._frame.Children.Append(self._img)
        self._frame.Children.Append(self._rect_el)
        self._frame.HorizontalAlignment = HorizontalAlignment.Center
        self._frame.VerticalAlignment = VerticalAlignment.Center

        hint = TextBlock()
        hint.Text = ("拖拽框选，松开即确认；关闭本窗口取消。"
                     "例图/锚点请框选尽量小的区域，锚点取填充色与背景色交界的竖直窄条。")
        hint.Margin = Thickness(12, 8, 12, 8)
        hint.Foreground = _brush(230, 255, 255, 255)

        outer = Grid()
        outer.Children.Append(self._frame)
        outer.Children.Append(_place_top_left(hint))
        outer.Background = _brush(255, 16, 16, 16)
        self.Content = outer
        outer.Loaded += self._on_loaded
        self.Closed += self._on_closed

        self._img.PointerPressed += self._on_press
        self._img.PointerMoved += self._on_move
        self._img.PointerReleased += self._on_release
        self._img.PointerCaptureLost += self._on_cancel_drag

    def activate(self) -> None:
        self.Activate()
        async def _load():
            try:
                stream = InMemoryRandomAccessStream()
                writer = DataWriter(stream.GetOutputStreamAt(0))
                writer.WriteBytes(self._png)
                await writer.StoreAsync()
                await writer.FlushAsync()
                writer.DetachStream()
                stream.Seek(0)
                bitmap = BitmapImage()
                await bitmap.SetSourceAsync(stream)
                self._img.Source = bitmap
            except Exception:
                self._finish(None)
        asyncui.create_task(_load())


    def _on_loaded(self, sender, args) -> None:
        try:
            scale = float(self._frame.XamlRoot.RasterizationScale) or 1.0
        except Exception:
            scale = 1.0
        sw, sh = self._shot.size
        avail_w = self._wa.Width / scale * _SCALES
        avail_h = self._wa.Height / scale * _SCALES
        self._f = min(avail_w / sw, avail_h / sh)
        self._frame.Width = sw * self._f
        self._frame.Height = sh * self._f


    def _pos(self, args):
        p = args.GetCurrentPoint(self._img).Position
        return float(p.X), float(p.Y)

    def _on_press(self, sender, args) -> None:
        self._img.CapturePointer(args.Pointer)
        self._press = self._pos(args)
        self._update_rect(self._press, self._press)

    def _on_move(self, sender, args) -> None:
        if self._press is None:
            return
        self._update_rect(self._press, self._pos(args))

    def _on_release(self, sender, args) -> None:
        if self._press is None:
            return
        release = self._pos(args)
        self._update_rect(self._press, release)
        rect = self._shot_rect(release)
        self._press = None
        self._img.ReleasePointerCapture(args.Pointer)
        self._finish(rect)

    def _on_cancel_drag(self, sender, args) -> None:
        self._press = None
        self._rect_el.Visibility = Visibility.Collapsed

    def _update_rect(self, a, b) -> None:
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        w, h = abs(b[0] - a[0]), abs(b[1] - a[1])
        self._rect_el.Margin = Thickness(x0, y0, 0, 0)
        self._rect_el.Width = w
        self._rect_el.Height = h
        self._rect_el.HorizontalAlignment = HorizontalAlignment.Left
        self._rect_el.VerticalAlignment = VerticalAlignment.Top
        self._rect_el.Visibility = 0

    def _shot_rect(self, release):
        if self._press is None or self._f <= 0:
            return None
        sw, sh = self._shot.size
        pts = [self._press, release]
        xs = [max(0.0, min(sw, p[0] / self._f)) for p in pts]
        ys = [max(0.0, min(sh, p[1] / self._f)) for p in pts]
        x0, x1 = int(min(xs)), int(max(xs))
        y0, y1 = int(min(ys)), int(max(ys))
        w, h = x1 - x0, y1 - y0
        if w < _MIN_SHOT_PX or h < _MIN_SHOT_PX:
            return None
        return [x0, y0, w, h]


    def _finish(self, shot_rect) -> None:
        self._done = True
        result = None
        if shot_rect is not None:
            x, y, w, h = shot_rect
            result = [x, y, w, h]
            if self._crop_mode:
                cropped = self._shot.crop((x, y, x + w, y + h))
                buf = io.BytesIO()
                cropped.save(buf, "PNG")
                result = (buf.getvalue(), [x, y, w, h])
        self.Close()
        try:
            self._on_done(result)
        except Exception:
            pass

    def _on_closed(self, sender, args) -> None:
        if not self._done:
            self._done = True
            try:
                self._on_done(None)
            except Exception:
                pass


def _place_top_left(el):
    el.HorizontalAlignment = HorizontalAlignment.Left
    el.VerticalAlignment = VerticalAlignment.Top
    return el


def _brush(a, r, g, b) -> SolidColorBrush:
    return SolidColorBrush(Color(a, r, g, b))
