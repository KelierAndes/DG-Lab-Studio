import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../app/tokens.dart';

/// 每个联动模块一枚纯线条标记：24×24 视框、1.4 描边、不填充、圆头。
///
/// 画出来而不是用 Material 图标库，是因为模块的语义（OSC 波形、气压表、
/// 手柄震动…）在图标库里凑不齐一套风格一致的；线条风格也贴合界面的浮雕观感。
class ModuleGlyph extends StatelessWidget {
  const ModuleGlyph({super.key, required this.id, this.size = 20, this.color});

  final String id;
  final double size;
  final Color? color;

  static const _known = <String>{
    'osc_bridge',
    'alice_cradle',
    'vision_link',
    'sound_link',
    'strength_logger',
    'margin_control',
    'xinput_oscillate',
    'config_init',
  };

  static bool knows(String id) => _known.contains(id);

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return CustomPaint(
      size: Size.square(size),
      painter: _GlyphPainter(
        id: id,
        stroke:
            color ??
            (t.isDark ? const Color(0xFFB9C4D4) : const Color(0xFF41506A)),
      ),
    );
  }
}

/// 列表用的方形小牌：浮雕底 + 居中的线条标记，尺寸与其它行的头像一致。
class ModuleTile extends StatelessWidget {
  const ModuleTile({
    super.key,
    required this.id,
    this.size = 28,
    this.active = false,
  });

  final String id;
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
      child: ModuleGlyph(id: id, size: size * .68),
    );
  }
}

class _GlyphPainter extends CustomPainter {
  _GlyphPainter({required this.id, required this.stroke});

  final String id;
  final Color stroke;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width / 24.0;
    final p = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4 * s
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..color = stroke;
    canvas.save();
    canvas.scale(s);
    switch (id) {
      case 'osc_bridge':
        _osc(canvas, p);
      case 'alice_cradle':
        _cradle(canvas, p);
      case 'vision_link':
        _eye(canvas, p);
      case 'sound_link':
        _speaker(canvas, p);
      case 'strength_logger':
        _bars(canvas, p);
      case 'margin_control':
        _gauge(canvas, p);
      case 'xinput_oscillate':
        _pad(canvas, p);
      case 'config_init':
        _sliders(canvas, p);
      default:
        _puzzle(canvas, p);
    }
    canvas.restore();
  }

  /// OSC：一段正弦 + 两个端点，表示收发的地址。
  void _osc(Canvas c, Paint p) {
    final w = Path()
      ..moveTo(3, 12)
      ..cubicTo(6, 4, 9, 4, 12, 12)
      ..cubicTo(15, 20, 18, 20, 21, 12);
    c.drawPath(w, p);
    c.drawCircle(const Offset(3, 12), 1.5, p);
    c.drawCircle(const Offset(21, 12), 1.5, p);
  }

  /// Alice in Cradle：摇篮的弧 + 里面的小圆。
  void _cradle(Canvas c, Paint p) {
    final cradle = Path()
      ..moveTo(4, 9)
      ..lineTo(20, 9)
      ..cubicTo(20, 17, 15.5, 20, 12, 20)
      ..cubicTo(8.5, 20, 4, 17, 4, 9)
      ..close();
    c.drawPath(cradle, p);
    c.drawCircle(const Offset(12, 13.5), 2.4, p);
    c.drawArc(
      Rect.fromLTWH(6, 1.6, 12, 8),
      math.pi * .06,
      math.pi * .88,
      false,
      p,
    );
  }

  /// 画面识别：眼睛轮廓 + 中间的识别框。
  void _eye(Canvas c, Paint p) {
    final lid = Path()
      ..moveTo(2.5, 12)
      ..cubicTo(6, 6.5, 18, 6.5, 21.5, 12)
      ..cubicTo(18, 17.5, 6, 17.5, 2.5, 12)
      ..close();
    c.drawPath(lid, p);
    c.drawRect(const Rect.fromLTWH(9.5, 9.5, 5, 5), p);
  }

  /// 音频联动：喇叭楔形 + 两道声波。
  void _speaker(Canvas c, Paint p) {
    final horn = Path()
      ..moveTo(3.5, 9.5)
      ..lineTo(7.5, 9.5)
      ..lineTo(12, 5.5)
      ..lineTo(12, 18.5)
      ..lineTo(7.5, 14.5)
      ..lineTo(3.5, 14.5)
      ..close();
    c.drawPath(horn, p);
    c.drawArc(
      const Rect.fromLTWH(13.5, 8, 6, 8),
      -math.pi / 3,
      math.pi * 2 / 3,
      false,
      p,
    );
    c.drawArc(
      const Rect.fromLTWH(13.5, 5, 10, 14),
      -math.pi / 3.4,
      math.pi * 2 / 3.4,
      false,
      p,
    );
  }

  /// 强度日志：三根柱 + 一条趋势线。
  void _bars(Canvas c, Paint p) {
    c.drawLine(const Offset(3.5, 20), const Offset(3.5, 13), p);
    c.drawLine(const Offset(9, 20), const Offset(9, 8), p);
    c.drawLine(const Offset(14.5, 20), const Offset(14.5, 15), p);
    c.drawLine(const Offset(20, 20), const Offset(20, 6), p);
    c.drawLine(const Offset(3.5, 5), const Offset(20, 5), p);
  }

  /// 灵猫气压：表盘弧 + 指针 + 外圈。
  void _gauge(Canvas c, Paint p) {
    c.drawArc(
      const Rect.fromLTWH(3.5, 5, 17, 17),
      math.pi * .78,
      math.pi * 1.44,
      false,
      p,
    );
    c.drawLine(const Offset(12, 13.5), const Offset(17, 9), p);
    c.drawCircle(const Offset(12, 13.5), 1.3, p);
  }

  /// 手柄震动：手柄轮廓 + 两侧震动弧。
  void _pad(Canvas c, Paint p) {
    final body = RRect.fromRectAndRadius(
      const Rect.fromLTWH(5, 8.5, 14, 8),
      const Radius.circular(4),
    );
    c.drawRRect(body, p);
    c.drawCircle(const Offset(9, 12.5), 1.2, p);
    c.drawCircle(const Offset(15, 12.5), 1.2, p);
    c.drawArc(
      const Rect.fromLTWH(.5, 9.5, 6, 6),
      -math.pi / 3,
      math.pi * .7,
      false,
      p,
    );
    c.drawArc(
      const Rect.fromLTWH(17.5, 9.5, 6, 6),
      math.pi - math.pi * .35,
      math.pi * .7,
      false,
      p,
    );
  }

  /// 初始化配置：三条滑杆。
  void _sliders(Canvas c, Paint p) {
    for (final (y, k) in const [(7.0, 8.0), (12.0, 15.5), (17.0, 10.5)]) {
      c.drawLine(Offset(3.5, y), Offset(20.5, y), p);
      c.drawCircle(Offset(k, y), 1.9, p);
    }
  }

  /// 未知模块：拼图块轮廓（与「模块」这一层的语义一致）。
  void _puzzle(Canvas c, Paint p) {
    final piece = Path()
      ..moveTo(4.5, 4.5)
      ..lineTo(10, 4.5)
      ..arcTo(const Rect.fromLTWH(10, 2.5, 4, 4), math.pi * 1.5, math.pi, false)
      ..lineTo(19.5, 4.5)
      ..lineTo(19.5, 10)
      ..arcTo(const Rect.fromLTWH(17.5, 10, 4, 4), math.pi * .5, math.pi, false)
      ..lineTo(19.5, 19.5)
      ..lineTo(4.5, 19.5)
      ..close();
    c.drawPath(piece, p);
  }

  @override
  bool shouldRepaint(_GlyphPainter old) => old.id != id || old.stroke != stroke;
}
