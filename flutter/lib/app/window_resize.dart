import 'package:flutter/widgets.dart';
import 'package:window_manager/window_manager.dart';

/// Which sides of the window a handle owns.
class WindowEdge {
  const WindowEdge({
    this.left = false,
    this.right = false,
    this.top = false,
    this.bottom = false,
  });

  final bool left, right, top, bottom;

  static const leftEdge = WindowEdge(left: true);
  static const rightEdge = WindowEdge(right: true);
  static const topEdge = WindowEdge(top: true);
  static const bottomEdge = WindowEdge(bottom: true);
  static const topLeftCorner = WindowEdge(left: true, top: true);
  static const topRightCorner = WindowEdge(right: true, top: true);
  static const bottomLeftCorner = WindowEdge(left: true, bottom: true);
  static const bottomRightCorner = WindowEdge(right: true, bottom: true);

  MouseCursor get cursor {
    if (left && top || right && bottom) {
      return SystemMouseCursors.resizeUpLeftDownRight;
    }
    if (right && top || left && bottom) {
      return SystemMouseCursors.resizeUpRightDownLeft;
    }
    return left || right
        ? SystemMouseCursors.resizeColumn
        : SystemMouseCursors.resizeRow;
  }
}

/// The window rect after dragging [edge] by [delta] away from [start].
///
/// Only the sides the handle owns move: the opposite edges stay pinned, which is
/// what makes a top-edge drag resize the top rather than slide the whole window
/// diagonally. [minSize] clamps by pushing the *dragged* side back, so the
/// anchored side never moves.
Rect resizeWindow({
  required Rect start,
  required WindowEdge edge,
  required Offset delta,
  required Size minSize,
}) {
  var left = start.left + (edge.left ? delta.dx : 0);
  var top = start.top + (edge.top ? delta.dy : 0);
  var right = start.right + (edge.right ? delta.dx : 0);
  var bottom = start.bottom + (edge.bottom ? delta.dy : 0);

  if (right - left < minSize.width) {
    if (edge.left) {
      left = right - minSize.width;
    } else {
      right = left + minSize.width;
    }
  }
  if (bottom - top < minSize.height) {
    if (edge.top) {
      top = bottom - minSize.height;
    } else {
      bottom = top + minSize.height;
    }
  }
  return Rect.fromLTRB(left, top, right, bottom);
}

/// Drag distance in screen space, recovered from a pointer position the window
/// itself keeps sliding out from under.
///
/// Pan deltas arrive in the window's own coordinate space. When the dragged edge
/// owns the origin (left, top, and the top-left corner), moving the window moves
/// the ruler with it, so the pointer reads almost stationary and the edge only
/// crawls. Adding back the origin we have already applied undoes that. Edges
/// that never move the origin simply get `+ 0` back.
Offset screenDelta({
  required Offset pointer,
  required Offset startPointer,
  required Rect start,
  required Rect applied,
}) => Offset(
  pointer.dx - startPointer.dx + applied.left - start.left,
  pointer.dy - startPointer.dy + applied.top - start.top,
);

/// Edge and corner drag zones that resize the frameless window.
///
/// The rect comes from the bounds captured when the pointer went down plus the
/// *total* drag, never from a fresh `getBounds()` per update: reading mid-drag
/// races the asynchronous window move, so every update resized from a stale rect
/// and the edge drifted away from the cursor.
class WindowResizeHandles extends StatefulWidget {
  const WindowResizeHandles({
    super.key,
    this.minSize = const Size(1000, 640),
    this.thickness = 14,
  });

  final Size minSize;

  /// Should cover the panel's outer padding, or there is a dead ring along the
  /// border where nothing is draggable.
  final double thickness;

  @override
  State<WindowResizeHandles> createState() => _WindowResizeHandlesState();
}

class _WindowResizeHandlesState extends State<WindowResizeHandles> {
  Rect? _startBounds;

  /// The rect the OS has actually been told to use. Awaited, so it is never
  /// ahead of the origin the next pointer event was measured against.
  Rect? _applied;
  Offset? _startPointer;
  WindowEdge? _edge;

  /// One move in flight at a time; later pointers only keep the newest.
  bool _moving = false;
  Offset? _queued;

  Future<void> _begin(WindowEdge edge, Offset at) async {
    _edge = edge;
    _startPointer = at;
    _startBounds = null;
    _applied = null;
    _queued = null;
    final bounds = await windowManager.getBounds();
    // A newer gesture — or none at all — owns the state by now.
    if (!mounted || _edge != edge || _startPointer != at) return;
    _startBounds = bounds;
    _applied = bounds;
  }

  void _update(Offset at) {
    if (_moving) {
      _queued = at;
      return;
    }
    _applyFrom(at);
  }

  Future<void> _applyFrom(Offset at) async {
    _moving = true;
    try {
      while (mounted && _edge != null) {
        final start = _startBounds;
        final applied = _applied;
        final origin = _startPointer;
        final edge = _edge;
        if (start == null ||
            applied == null ||
            origin == null ||
            edge == null) {
          break;
        }
        final next = resizeWindow(
          start: start,
          edge: edge,
          delta: screenDelta(
            pointer: at,
            startPointer: origin,
            start: start,
            applied: applied,
          ),
          minSize: widget.minSize,
        );
        if (next != applied) {
          _applied = next;
          // Awaiting keeps `_applied` honest: the origin has really moved before
          // the next pointer event is measured against it.
          await windowManager.setBounds(next);
        }
        final queued = _queued;
        if (queued == null) break;
        _queued = null;
        at = queued;
      }
    } finally {
      _moving = false;
    }
  }

  void _end() {
    _edge = null;
    _startPointer = null;
    _startBounds = null;
    _applied = null;
    _queued = null;
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, box) {
        final w = box.maxWidth;
        final h = box.maxHeight;
        final t = widget.thickness;
        Widget handle(Rect hit, WindowEdge edge) => Positioned.fromRect(
          rect: hit,
          // Opaque: a resize must not also start a canvas marquee underneath.
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onPanStart: (d) => _begin(edge, d.globalPosition),
            onPanUpdate: (d) => _update(d.globalPosition),
            onPanEnd: (_) => _end(),
            onPanCancel: _end,
            child: MouseRegion(
              cursor: edge.cursor,
              child: const SizedBox.expand(),
            ),
          ),
        );
        return Stack(
          children: [
            handle(Rect.fromLTRB(t, 0, w - t, t), WindowEdge.topEdge),
            handle(Rect.fromLTRB(t, h - t, w - t, h), WindowEdge.bottomEdge),
            handle(Rect.fromLTRB(0, t, t, h - t), WindowEdge.leftEdge),
            handle(Rect.fromLTRB(w - t, t, w, h - t), WindowEdge.rightEdge),
            handle(Rect.fromLTRB(0, 0, t, t), WindowEdge.topLeftCorner),
            handle(Rect.fromLTRB(w - t, 0, w, t), WindowEdge.topRightCorner),
            handle(Rect.fromLTRB(0, h - t, t, h), WindowEdge.bottomLeftCorner),
            handle(
              Rect.fromLTRB(w - t, h - t, w, h),
              WindowEdge.bottomRightCorner,
            ),
          ],
        );
      },
    );
  }
}
