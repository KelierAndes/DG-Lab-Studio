import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../app/tokens.dart';

/// 每类设备一枚纯线条标记：24×24 视框、1.4 描边、不填充、圆头。
///
/// 与 [ModuleGlyph] 同一套画法：郊狼（电刺激）、负鼠（振动）、灵猫（气压）的
/// 形状在 Material 图标库里凑不出一致风格，画出来还能一眼分清三类设备。
class DeviceGlyph extends StatelessWidget {
  const DeviceGlyph({
    super.key,
    required this.family,
    this.size = 20,
    this.color,
  });

  /// `family_of(type)` 的结果：COYOTE / OVC / BMTR。
  final String family;
  final double size;
  final Color? color;

  static const families = <String>{'COYOTE', 'OVC', 'BMTR'};

  static bool knows(String family) => families.contains(family.toUpperCase());

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return CustomPaint(
      size: Size.square(size),
      painter: _DevicePainter(
        family: family.toUpperCase(),
        stroke:
            color ??
            (t.isDark ? const Color(0xFFB9C4D4) : const Color(0xFF41506A)),
      ),
    );
  }
}

/// 列表与卡头用的小牌：浮雕底 + 居中的设备线条标记。
class DeviceTile extends StatelessWidget {
  const DeviceTile({
    super.key,
    required this.family,
    this.size = 28,
    this.active = false,
  });

  final String family;
  final double size;
  final bool active;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: active ? t.surfaceRaised : t.well,
        borderRadius: BorderRadius.circular(size * .28),
        border: Border.all(
          color: active ? t.accent.withAlpha(120) : t.hairline,
        ),
        boxShadow: t.raised(depth: .5),
      ),
      child: DeviceGlyph(family: family, size: size * .66),
    );
  }
}

class _DevicePainter extends CustomPainter {
  _DevicePainter({required this.family, required this.stroke});

  final String family;
  final Color stroke;

  static const _s = 1.4;

  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = stroke
      ..style = PaintingStyle.stroke
      ..strokeWidth = _s
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final u = size.width / 24.0;
    Offset pt(double x, double y) => Offset(x * u, y * u);
    Rect box(double x, double y, double w, double h) =>
        Rect.fromLTWH(x * u, y * u, w * u, h * u);

    switch (family) {
      case 'OVC':
        _vibrationUnit(canvas, p, pt, box, u);
      case 'BMTR':
        _pressureGauge(canvas, p, pt, box, u);
      default:
        _stimBox(canvas, p, pt, box, u);
    }
  }

  /// 郊狼：主机方块 + 两根输出线 + 一段脉冲折线（电刺激）。
  void _stimBox(
    Canvas c,
    Paint p,
    Offset Function(double, double) pt,
    Rect Function(double, double, double, double) box,
    double u,
  ) {
    c.drawRRect(
      RRect.fromRectAndRadius(box(3, 7, 11, 10), Radius.circular(2.4 * u)),
      p,
    );
    c.drawLine(pt(6, 10.5), pt(11, 10.5), p);
    c.drawCircle(pt(8.5, 13.8), 1.1 * u, p);
    // 两根导线从右上角绕出去。
    final lead = Path()
      ..moveTo(14 * u, 10 * u)
      ..cubicTo(17 * u, 10 * u, 17 * u, 5.5 * u, 20 * u, 5.5 * u);
    final lead2 = Path()
      ..moveTo(14 * u, 13.5 * u)
      ..cubicTo(18 * u, 13.5 * u, 18 * u, 9 * u, 21 * u, 9 * u);
    c.drawPath(lead, p);
    c.drawPath(lead2, p);
    c.drawCircle(pt(20.6, 5.5), 0.9 * u, p);
    c.drawCircle(pt(21.6, 9), 0.9 * u, p);
  }

  /// 负鼠：手柄机身 + 方向键与两枚圆键 + 两侧振动弧。
  void _vibrationUnit(
    Canvas c,
    Paint p,
    Offset Function(double, double) pt,
    Rect Function(double, double, double, double) box,
    double u,
  ) {
    final body = Path()
      ..moveTo(6 * u, 8.5 * u)
      ..lineTo(18 * u, 8.5 * u)
      ..cubicTo(20.6 * u, 8.5 * u, 21.4 * u, 12 * u, 20.2 * u, 15 * u)
      ..cubicTo(19.2 * u, 17.6 * u, 16.6 * u, 17.9 * u, 15.4 * u, 15.8 * u)
      ..lineTo(8.6 * u, 15.8 * u)
      ..cubicTo(7.4 * u, 17.9 * u, 4.8 * u, 17.6 * u, 3.8 * u, 15 * u)
      ..cubicTo(2.6 * u, 12 * u, 3.4 * u, 8.5 * u, 6 * u, 8.5 * u)
      ..close();
    c.drawPath(body, p);
    // 十字方向键
    c.drawLine(pt(7.6, 11), pt(7.6, 14), p);
    c.drawLine(pt(6.1, 12.5), pt(9.1, 12.5), p);
    // 两枚圆键
    c.drawCircle(pt(15.4, 11.6), 1.05 * u, p);
    c.drawCircle(pt(17.9, 13), 1.05 * u, p);
    // 振动弧：机身两侧各两道
    for (final r in [3.4, 5.6]) {
      final rect = box(12 - r / 2, 3.4 - r / 2 + 2.4, r * 2, r * 2);
      c.drawArc(rect, math.pi * 1.18, math.pi * 0.5, false, p);
    }
    for (final r in [3.4, 5.6]) {
      final rect = box(12 - r / 2, 3.4 - r / 2 + 2.4, r * 2, r * 2);
      c.drawArc(rect, math.pi * 1.32, math.pi * 0.5, false, p);
    }
  }

  /// 灵猫：气压表盘 + 指针 + 进气软管。
  void _pressureGauge(
    Canvas c,
    Paint p,
    Offset Function(double, double) pt,
    Rect Function(double, double, double, double) box,
    double u,
  ) {
    c.drawCircle(pt(12, 13), 6.4 * u, p);
    c.drawArc(
      box(7.2, 8.2, 9.6, 9.6),
      math.pi * 1.15,
      math.pi * 0.75,
      false,
      p,
    );
    c.drawArc(
      box(7.2, 8.2, 9.6, 9.6),
      math.pi * 1.95,
      math.pi * 0.75,
      false,
      p,
    );
    c.drawLine(pt(12, 13), pt(15, 10.4), p);
    c.drawCircle(pt(12, 13), 1.0 * u, p);
    // 顶部接头与软管
    c.drawLine(pt(12, 6.6), pt(12, 4.6), p);
    final hose = Path()
      ..moveTo(12 * u, 4.6 * u)
      ..cubicTo(12 * u, 2.2 * u, 17 * u, 2.4 * u, 19.4 * u, 3.6 * u);
    c.drawPath(hose, p);
    c.drawRRect(
      RRect.fromRectAndRadius(
        box(19.2, 2.6, 2.6, 2.2),
        Radius.circular(0.7 * u),
      ),
      p,
    );
  }

  @override
  bool shouldRepaint(_DevicePainter old) =>
      old.family != family || old.stroke != stroke;
}
