import 'dart:math' as math;

import 'package:flutter/rendering.dart';

import '../app/tokens.dart';
import 'graph_model.dart';

/// Dot lattice that pans and zooms with the graph.
class GridPainter extends CustomPainter {
  GridPainter({
    required this.pan,
    required this.scale,
    required this.tokens,
    this.enabled = true,
  });

  final Offset pan;
  final double scale;
  final AppTokens tokens;
  final bool enabled;

  @override
  void paint(Canvas canvas, Size size) {
    if (!enabled) return;
    const base = 26.0;
    var step = base * scale;
    // Keep the lattice legible: double the spacing until dots stay >= 12px apart.
    while (step < 12) {
      step *= 2;
    }
    final paint = Paint()..color = tokens.gridDot;
    final r = math.max(0.6, 0.85 * scale);
    final x0 = pan.dx % step;
    final y0 = pan.dy % step;
    for (var x = x0; x < size.width; x += step) {
      for (var y = y0; y < size.height; y += step) {
        canvas.drawCircle(Offset(x, y), r, paint);
      }
    }
    // Slightly brighter every 5th intersection reads as a major lattice.
    final major = Paint()..color = tokens.gridDot.withAlpha(2);
    final majorStep = step * 5;
    final mx0 = pan.dx % majorStep;
    final my0 = pan.dy % majorStep;
    for (var x = mx0; x < size.width; x += majorStep) {
      for (var y = my0; y < size.height; y += majorStep) {
        canvas.drawCircle(Offset(x, y), r * 1.9, major);
      }
    }
  }

  @override
  bool shouldRepaint(GridPainter old) =>
      old.pan != pan ||
      old.scale != scale ||
      old.tokens != tokens ||
      old.enabled != enabled;
}

/// Draws every connector in viewport space so stroke width stays constant
/// regardless of zoom.
class EdgePainter extends CustomPainter {
  EdgePainter({required this.graph, required this.tokens});

  final GraphController graph;
  final AppTokens tokens;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    for (final e in graph.edges) {
      final g = graph.edgeGeometry(e);
      if (g == null) continue;
      final a = graph.toView(g.start);
      final b = graph.toView(g.end);
      final isExec = graph.specOf(e.from)?.isExec ?? false;
      _stroke(
        canvas,
        a,
        b,
        _widthFor(e, isExec),
        _colorFor(e, isExec),
        graph.mirror,
      );
      // Execution wires carry a direction; data wires do not.
      if (isExec) _arrow(canvas, a, b, _colorFor(e, true), graph.mirror);
      _cap(canvas, a, graph.specOf(e.from)!, false);
      _cap(canvas, b, graph.specOf(e.to)!, true);
      // 选中的线只加粗变色，**不画那颗 ✕**：它是画上去的，点不到，
      // 留着就是「看着能按其实按不了」。断线用选中后 Delete / 右键 / 右键框选。
    }

    final p = graph.pending;
    if (p != null) {
      final originSpec = graph.specOf(p.origin);
      final originWorld = graph.portWorld(p.origin);
      if (originSpec != null && originWorld != null) {
        final from = graph.toView(originWorld);
        final to = p.hover != null
            ? graph.toView(graph.portWorld(p.hover!)!)
            : graph.toView(p.cursor);
        final paint = Paint()
          ..color = p.hover == null ? tokens.edgeActive : tokens.accent
          ..strokeWidth = 1.8
          ..style = PaintingStyle.stroke
          ..strokeCap = StrokeCap.round;
        final geo = p.dir == PortDir.output
            ? EdgeGeometry(from, to, mirror: graph.mirror)
            : EdgeGeometry(to, from, mirror: graph.mirror);
        final path = Path()
          ..moveTo(geo.start.dx, geo.start.dy)
          ..cubicTo(
            geo.c1.dx,
            geo.c1.dy,
            geo.c2.dx,
            geo.c2.dy,
            geo.end.dx,
            geo.end.dy,
          );
        canvas.drawPath(_dashed(path), paint);
        if (p.hover == null) {
          canvas.drawCircle(to, 3.6, Paint()..color = tokens.edgeActive);
        }
      }
    }
    canvas.restore();
  }

  double _widthFor(GraphEdge e, bool isExec) {
    final base = isExec ? 0.7 : 0.0;
    if (graph.selectedEdge == e.id) return 2.4 + base;
    if (graph.hoveredEdge == e.id) return 2.0 + base;
    return 1.4 + base;
  }

  Color _colorFor(GraphEdge e, bool isExec) {
    if (graph.selectedEdge == e.id) return tokens.edgeActive;
    final spec = graph.specOf(e.from);
    if (graph.hoveredEdge == e.id && spec != null) {
      return tokens.portColor(spec.kind);
    }
    if (isExec) return tokens.portColor(PortKind.exec).withAlpha(200);
    // 数据连线按端口类型上色：一画布灰线里看不出谁在传什么，
    // 同一条线从出脚到落脚都是那一类的颜色，一眼就能顺着找到头。
    return spec == null ? tokens.edge : tokens.portColor(spec.kind).withAlpha(230);
  }

  /// Filled triangle at the target end, aligned with the curve's tangent.
  void _arrow(
    Canvas canvas,
    Offset a,
    Offset b,
    Color color, [
    bool mirror = false,
  ]) {
    final geo = EdgeGeometry(a, b, mirror: mirror);
    final dir = (geo.end - geo.c2);
    if (dir.distanceSquared < 1e-6) return;
    final u = dir / dir.distance;
    final tip = b + u * 1.5;
    final base = tip - u * 8;
    final n = Offset(-u.dy, u.dx) * 3.6;
    canvas.drawPath(
      Path()
        ..moveTo(tip.dx, tip.dy)
        ..lineTo((base + n).dx, (base + n).dy)
        ..lineTo((base - n).dx, (base - n).dy)
        ..close(),
      Paint()..color = color,
    );
  }

  void _stroke(
    Canvas canvas,
    Offset a,
    Offset b,
    double w,
    Color color, [
    bool mirror = false,
  ]) {
    final geo = EdgeGeometry(a, b, mirror: mirror);
    final path = Path()
      ..moveTo(geo.start.dx, geo.start.dy)
      ..cubicTo(
        geo.c1.dx,
        geo.c1.dy,
        geo.c2.dx,
        geo.c2.dy,
        geo.end.dx,
        geo.end.dy,
      );
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..strokeWidth = w
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round,
    );
  }

  void _cap(Canvas canvas, Offset at, PortSpec spec, bool input) {
    if (spec.isExec) return; // the triangle pin is already the arrow
    final color = tokens.portColor(spec.kind);
    canvas.drawCircle(at, 2.6, Paint()..color = color.withAlpha(150));
  }

  Path _dashed(Path src, {double dash = 6, double gap = 5}) {
    final out = Path();
    for (final m in src.computeMetrics()) {
      var travelled = 0.0;
      var on = true;
      while (travelled < m.length) {
        final step = math.min(on ? dash : gap, m.length - travelled);
        if (on) {
          out.addPath(m.extractPath(travelled, travelled + step), Offset.zero);
        }
        travelled += step;
        on = !on;
      }
    }
    return out;
  }

  @override
  bool shouldRepaint(EdgePainter old) => true;
}

/// Marquee rectangle + soft vignette above the node layer.
class CanvasOverlayPainter extends CustomPainter {
  CanvasOverlayPainter({required this.marquee, required this.tokens});

  final Rect? marquee;
  final AppTokens tokens;

  @override
  void paint(Canvas canvas, Size size) {
    final m = marquee;
    if (m == null) return;
    canvas.drawRect(m, Paint()..color = tokens.edgeActive.withAlpha(22));
    canvas.drawRect(
      m,
      Paint()
        ..color = tokens.edgeActive.withAlpha(140)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1,
    );
  }

  @override
  bool shouldRepaint(CanvasOverlayPainter old) => old.marquee != marquee;
}
