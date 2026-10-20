import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/window_resize.dart';

void main() {
  const start = Rect.fromLTWH(620, 346, 1320, 860);
  const min = Size(1000, 640);

  Rect drag(WindowEdge edge, double dx, double dy) => resizeWindow(
        start: start,
        edge: edge,
        delta: Offset(dx, dy),
        minSize: min,
      );

  group('each handle owns only its own sides', () {
    test('the right edge grows sideways and leaves the rest alone', () {
      expect(drag(WindowEdge.rightEdge, 40, 999), const Rect.fromLTWH(620, 346, 1360, 860));
    });

    test('the left edge moves the left edge, the right side stays pinned', () {
      expect(drag(WindowEdge.leftEdge, -40, 999), const Rect.fromLTWH(580, 346, 1360, 860));
    });

    test('the top edge does not slide the window sideways', () {
      expect(drag(WindowEdge.topEdge, 999, -40), const Rect.fromLTWH(620, 306, 1320, 900));
    });

    test('the bottom edge leaves the top and both sides untouched', () {
      expect(drag(WindowEdge.bottomEdge, 999, 60), const Rect.fromLTWH(620, 346, 1320, 920));
    });

    test('corners move both of their sides', () {
      expect(drag(WindowEdge.topLeftCorner, -10, -20), const Rect.fromLTWH(610, 326, 1330, 880));
      expect(drag(WindowEdge.bottomRightCorner, 10, 20), const Rect.fromLTWH(620, 346, 1330, 880));
    });
  });

  group('the minimum size clamps instead of freezing', () {
    test('pushing the right edge past the minimum pins the width', () {
      final r = drag(WindowEdge.rightEdge, -900, 0);
      expect(r.width, 1000);
      expect(r.left, 620, reason: 'the anchored side never moves');
      expect(r.right, 1620);
    });

    test('pushing the left edge past the minimum pins the right edge', () {
      final r = drag(WindowEdge.leftEdge, 1200, 0);
      expect(r.width, 1000);
      expect(r.right, 1940);
      expect(r.left, 940);
    });

    test('a corner clamps both axes independently', () {
      final r = drag(WindowEdge.bottomRightCorner, -4000, -4000);
      expect(r.size, min);
      expect(r.topLeft, start.topLeft);
    });

    test('a side handle ignores the axis it does not own', () {
      final r = drag(WindowEdge.rightEdge, 200, -5000);
      expect(r.width, 1520);
      expect(r.height, 860, reason: 'no vertical resize from a left/right handle');
      expect(r.top, 346);
    });
  });

  test('a zero drag is a no-op', () {
    expect(drag(WindowEdge.bottomRightCorner, 0, 0), start);
  });

  group('a drag measured while the window moves under the cursor', () {
    /// Replays a real drag: each pointer event is reported against the origin
    /// the window had reached when that event was delivered.
    Rect trace(WindowEdge edge, List<Offset> screenPoints, {Rect from = start}) {
      final press = screenPoints.first - from.topLeft;
      var applied = from;
      for (final screen in screenPoints) {
        applied = resizeWindow(
          start: from,
          edge: edge,
          delta: screenDelta(
            pointer: screen - applied.topLeft,
            startPointer: press,
            start: from,
            applied: applied,
          ),
          minSize: min,
        );
      }
      return applied;
    }

    test('the top edge tracks the cursor one for one', () {
      final r = trace(WindowEdge.topEdge, const [
        Offset(900, 346),
        Offset(900, 374),
        Offset(900, 402),
        Offset(900, 430),
        Offset(900, 458),
      ]);
      expect(r.top, 458, reason: 'the bottom edge was the anchor all along');
      expect(r.height, 748);
      expect(r.left, 620);
      expect(r.width, 1320);
    });

    test('the left edge tracks too', () {
      final r = trace(WindowEdge.leftEdge, const [
        Offset(620, 700),
        Offset(580, 700),
        Offset(540, 700),
        Offset(500, 700),
      ]);
      expect(r.left, 500);
      expect(r.width, 1440);
      expect(r.right, 1940);
    });

    test('an origin edge would stall without the shift added back', () {
      const atRest = Rect.fromLTWH(0, 0, 1000, 800);
      // The cursor has moved 100 down and the top has already followed it, so
      // the window reports the pointer exactly where it went down.
      const applied = Rect.fromLTWH(0, 100, 1000, 700);
      expect(
        screenDelta(pointer: const Offset(500, 0), startPointer: const Offset(500, 0), start: atRest, applied: applied),
        const Offset(0, 100),
      );
    });

    test('an edge that never moves the origin sees no correction', () {
      const atRest = Rect.fromLTWH(0, 0, 1000, 800);
      const applied = Rect.fromLTWH(0, 0, 1100, 800);
      expect(
        screenDelta(pointer: const Offset(999, 137), startPointer: const Offset(999, 37), start: atRest, applied: applied),
        const Offset(0, 100),
      );
    });

    test('a corner held at the minimum height stops tracking the cursor', () {
      final r = trace(WindowEdge.topLeftCorner, const [
        Offset(620, 346),
        Offset(720, 446),
        Offset(820, 546),
        Offset(920, 646),
      ]);
      expect(r.size, const Size(1020, 640));
      expect(r.top, 566, reason: 'clamped up by the minimum, not left at the cursor');
      expect(r.bottomRight, const Offset(1940, 1206));
    });
  });
}
