import 'package:flutter/material.dart';

import '../app/tokens.dart';

/// 负鼠按键的可视化绑定：机身画在中间，11 个按键位用引线连到旁边的按钮上，
/// 点按钮挑动作；设备真按下某位时，机身上那颗键的位置会亮一下。
///
/// 尺寸与配色按官方离线模式页逐处对齐：
/// 设计坐标经 `(x+30)*1.5 / (y-78)*1.5` 落到 1040×340 的画布上，机身是切角八边形
/// 加两道内斜面，中间小屏 + 十字键 + 四枚面键 + 三枚小键，引线 1 px、端点 5.7 px 圆点。
/// 这块画布**不跟主题走**（旧版也是写死的深色线稿），换色会让它和机身示意图混在一起。
class BindingPad extends StatelessWidget {
  const BindingPad({
    super.key,
    required this.buttons,
    required this.labelOf,
    required this.onTapBit,
    this.pressed = const {},
    this.warning = const {},
  });

  /// 引擎给的按键位：`[{bit, label}]`，按 bit 找它在机身上的位置。
  final List<Map<String, Object?>> buttons;
  final String Function(String bit) labelOf;
  final void Function(String bit) onTapBit;

  /// 当前按下的位（bit 号）。
  final Set<int> pressed;

  /// 绑了但动作缺失（例如模块没启用）的位。
  final Set<int> warning;

  static const _cw = 1040.0;
  static const _ch = 340.0;

  // 旧版写死的配色（不随明暗主题变）。
  static const _bg = Color(0xFF0B0B0B);
  static const _edge1 = Color(0xFF8E8E8E);
  static const _edge2 = Color(0xFF585858);
  static const _edge3 = Color(0xFF414141);
  static const _art = Color(0xFF8F8F8F);
  static const _art2 = Color(0xFF6F6F6F);
  static const _glyph = Color(0xFFC6C6C6);
  static const _glowFill = Color(0x6E5ABEFF);
  static const _glowStroke = Color(0xEB8CD2FF);

  /// 设计坐标 → 画布像素（旧版 `_bv_pt`）。
  static double px(double x) => (x + 30) * 1.5;
  static double py(double y) => (y - 78) * 1.5;

  /// 按键位 → 机身上那颗键的锚点（设计坐标）：发光圆心用它，引线终点在它朝标签
  /// 那一侧的边缘上（见 [_leadOffset]）。
  static const _anchors = <int, List<double>>{
    8: [246, 138],
    9: [246, 192],
    10: [220, 165],
    11: [272, 165],
    14: [386, 141],
    15: [361, 165],
    12: [411, 165],
    13: [386, 189],
    0: [291, 202],
    1: [341, 202],
    2: [316, 202],
  };

  /// 发光圆半径（设计单位）：方向键与小键 9，四枚面键 12.5。
  static const _glowR = <int, double>{
    8: 9,
    9: 9,
    10: 9,
    11: 9,
    0: 9,
    1: 9,
    2: 9,
    14: 12.5,
    15: 12.5,
    12: 12.5,
    13: 12.5,
  };

  /// 引线终点相对锚点的偏移（设计坐标）。官方图里点不压在字母上：面键的点落在
  /// 圆的右缘（标签都在右侧），方向键落在左缘，底部三枚小键落在下缘。
  static const _leadOffset = <int, List<double>>{
    8: [-9, -2],
    9: [-9, 2],
    10: [-9, 0],
    11: [-9, 0],
    14: [12, 0],
    15: [12, 0],
    12: [12, 0],
    13: [12, 0],
    0: [-6, 7],
    1: [6, 7],
    2: [0, 8],
  };

  /// 机身左右缘（画布像素）：设计坐标 188 / 444 换算过来是 327 / 711。
  static const _bodyL = 327.0;
  static const _bodyR = 711.0;

  /// 标签框离机身的固定间隔；框靠机身那侧的边钉在这个距离上，文字长就往外加长。
  static const _bodyGap = 16.0;

  /// 按键位 → 按钮框靠机身那侧的锚边（画布像素）与竖直中心。
  /// 左列钉右边、右列钉左边：整列原来钉在画布两端，标签被推到离机身很远的地方。
  static final _rows = <int, _Row>{
    8: _Row(right: _bodyL - _bodyGap, cy: 39),
    10: _Row(right: _bodyL - _bodyGap, cy: 94.5),
    11: _Row(right: _bodyL - _bodyGap, cy: 150),
    9: _Row(right: _bodyL - _bodyGap, cy: 205.5),
    14: _Row(left: _bodyR + _bodyGap, cy: 39),
    15: _Row(left: _bodyR + _bodyGap, cy: 94.5),
    12: _Row(left: _bodyR + _bodyGap, cy: 150),
    13: _Row(left: _bodyR + _bodyGap, cy: 205.5),
    0: _Row(right: px(282) - _bodyGap, cy: 243),
    1: _Row(left: px(350) + _bodyGap, cy: 243),
    2: _Row(center: px(316), cy: 276),
  };

  static const _rowH = 30.0;
  static const _rowMinW = 70.0;
  static const _rowMaxW = 300.0;
  static const _rowFont = 15.0;

  /// 框宽照文字量：「B 切换上一个波形」「发送 OSC /avatar/parameters/…」这种
  /// 长名字不能截成省略号，但也得有上限，否则框会把机身挤出去。
  static double rowWidth(String text) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(
          fontSize: _rowFont,
          height: 1.1,
          letterSpacing: 0,
          color: Colors.white,
        ),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 1,
    )..layout();
    final w = tp.width + 20;
    tp.dispose();
    return w.clamp(_rowMinW, _rowMaxW);
  }

  static Rect rowRect(int bit, double w) {
    final r = _rows[bit]!;
    final x = r.center != null
        ? r.center! - w / 2
        : (r.right != null ? r.right! - w : r.left!);
    return Rect.fromLTWH(x, r.cy - _rowH / 2, w, _rowH);
  }

  /// 引线的折点：水平段走标签自己的高度，45° 段吃掉竖直方向的差。
  ///
  /// 反过来先斜再平，多条线会在键那一侧共用同一段水平线（就是「引导线重叠」）；
  /// 每个标签的高度都不同，水平段因此天然不重合。
  static Offset leadKnee(Offset start, Offset to) {
    final dir = to.dx >= start.dx ? 1 : -1;
    var x = to.dx - dir * (to.dy - start.dy).abs();
    if ((x - start.dx) * dir < 0) x = start.dx;
    return Offset(x, start.dy);
  }

  /// 面键上的字母（画布像素字号 16，圆心左上偏移 -14 / -12）。
  static const _letters = <int, String>{14: 'G', 15: 'D', 12: 'B', 13: 'A'};

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, box) {
        final k = box.maxWidth.isFinite ? box.maxWidth / _cw : 1.0;
        final t = AppScope.of(context);
        final boxes = <int, Rect>{};
        for (final b in buttons) {
          final bit = int.tryParse('${b['bit']}') ?? -1;
          if (!_rows.containsKey(bit)) continue;
          boxes[bit] = rowRect(bit, rowWidth(labelOf('$bit')));
        }
        final kids = <Widget>[
          Positioned.fill(
            child: CustomPaint(
              painter: _PadPainter(
                k,
                boxes,
                pressed,
                warning,
                t.textSecondary,
                t.accent,
              ),
            ),
          ),
        ];
        for (final entry in boxes.entries) {
          final bit = entry.key;
          final r = entry.value;
          final b = buttons.firstWhere(
            (x) => (int.tryParse('${x['bit']}') ?? -1) == bit,
          );
          kids.add(
            Positioned(
              left: r.left * k,
              top: r.top * k,
              width: r.width * k,
              height: r.height * k,
              child: _PadButton(
                key: ValueKey('binding-$bit'),
                name: '${b['label']}',
                bit: bit,
                text: labelOf('$bit'),
                lit: pressed.contains(bit),
                warning: warning.contains(bit),
                onTap: () => onTapBit('$bit'),
              ),
            ),
          );
        }
        return SizedBox(
          width: _cw * k,
          height: _ch * k,
          child: Stack(clipBehavior: Clip.none, children: kids),
        );
      },
    );
  }
}

class _Row {
  const _Row({this.left, this.right, this.center, required this.cy});
  final double? left, right, center;
  final double cy;
}

class _PadButton extends StatefulWidget {
  const _PadButton({
    super.key,
    required this.name,
    required this.bit,
    required this.text,
    required this.lit,
    required this.warning,
    required this.onTap,
  });

  final String name;
  final int bit;
  final String text;
  final bool lit;
  final bool warning;
  final VoidCallback onTap;

  @override
  State<_PadButton> createState() => _PadButtonState();
}

class _PadButtonState extends State<_PadButton> {
  bool _hot = false;

  @override
  Widget build(BuildContext context) {
    // 旧版按钮：142×30，#141414 底、#585858 边、15 px 米色文字；悬停提亮一档。
    final bg = widget.lit
        ? const Color(0xFF0D0D0D)
        : (_hot ? const Color(0xFF1F1F1F) : const Color(0xFF141414));
    final border = widget.warning
        ? const Color(0xFFBE5A5A)
        : (widget.lit
              ? const Color(0xFF9A9A9A)
              : (_hot ? const Color(0xFF7A7A7A) : const Color(0xFF585858)));
    final fg = widget.lit ? const Color(0xFFEADFBC) : const Color(0xFFD9CFA6);
    return Tooltip(
      // HOME 连点 5 下进搜索态这件事，旧版只挂在这颗键的提示上。
      message: widget.bit == 2
          ? 'HOME 键连点 5 下，设备进入可被插槽搜索连接的状态'
          : '${widget.name} · ${widget.text}',
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hot = true),
        onExit: (_) => setState(() => _hot = false),
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: widget.onTap,
          child: Container(
            alignment: Alignment.center,
            padding: const EdgeInsets.symmetric(horizontal: 6),
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: border),
            ),
            // 序号角标画在框外（见 BindingPad.build），框里只留文字。
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.center,
              child: Text(
                widget.text,
                maxLines: 1,
                softWrap: false,
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 15, height: 1.1, color: fg),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _PadPainter extends CustomPainter {
  _PadPainter(
    this.k,
    this.boxes,
    this.pressed,
    this.warning,
    this.lead,
    this.dot,
  );

  final double k;

  /// 每个按键位的按钮框（画布像素）：引线要从文字末端出发，得知道框实际多宽。
  final Map<int, Rect> boxes;
  final Set<int> pressed;
  final Set<int> warning;

  /// 引线与端点跟主题走：深色下要够亮才看得见，浅色下不能糊成一片。
  /// 机身线稿仍是写死的深色（旧版就是固定深色的一块设备示意图）。
  final Color lead;
  final Color dot;

  // 设计坐标、配色与锚点都只有一份定义，在 BindingPad 上；这里转发一下，
  // 画笔和按钮框才不会各算各的。
  static const _anchors = BindingPad._anchors;
  static const _glowR = BindingPad._glowR;
  static const _leadOffset = BindingPad._leadOffset;
  static final _rows = BindingPad._rows;
  static const _letters = BindingPad._letters;
  static const _bg = BindingPad._bg;
  static const _edge1 = BindingPad._edge1;
  static const _edge2 = BindingPad._edge2;
  static const _edge3 = BindingPad._edge3;
  static const _art = BindingPad._art;
  static const _art2 = BindingPad._art2;
  static const _glyph = BindingPad._glyph;
  static const _glowFill = BindingPad._glowFill;
  static const _glowStroke = BindingPad._glowStroke;

  double px(double x) => BindingPad.px(x);
  double py(double y) => BindingPad.py(y);

  void _poly(Canvas c, Path p, {Color? fill, Color? stroke, double w = 1.2}) {
    if (fill != null) c.drawPath(p, Paint()..color = fill);
    if (stroke != null) {
      c.drawPath(
        p,
        Paint()
          ..color = stroke
          ..style = PaintingStyle.stroke
          ..strokeWidth = w
          ..strokeJoin = StrokeJoin.round,
      );
    }
  }

  /// 切角八边形（旧版 `octagon(a,b,c,d,k)`），给的是设计坐标。
  Path _octagon(double a, double b, double cc, double d, double cut) {
    final pts = <Offset>[
      Offset(a + cut, b),
      Offset(cc - cut, b),
      Offset(cc, b + cut),
      Offset(cc, d - cut),
      Offset(cc - cut, d),
      Offset(a + cut, d),
      Offset(a, d - cut),
      Offset(a, b + cut),
    ];
    return Path()
      ..addPolygon([for (final p in pts) Offset(px(p.dx), py(p.dy))], true);
  }

  /// 十二点十字（旧版 `cross(half, arm)`），给的是设计坐标。
  Path _cross(double cx, double cy, double half, double arm) {
    final pts = <Offset>[
      Offset(cx - arm, cy - half),
      Offset(cx + arm, cy - half),
      Offset(cx + arm, cy - arm),
      Offset(cx + half, cy - arm),
      Offset(cx + half, cy + arm),
      Offset(cx + arm, cy + arm),
      Offset(cx + arm, cy + half),
      Offset(cx - arm, cy + half),
      Offset(cx - arm, cy + arm),
      Offset(cx - half, cy + arm),
      Offset(cx - half, cy - arm),
      Offset(cx - arm, cy - arm),
    ];
    return Path()
      ..addPolygon([for (final p in pts) Offset(px(p.dx), py(p.dy))], true);
  }

  Path _tri(List<double> p) => Path()
    ..moveTo(px(p[0]), py(p[1]))
    ..lineTo(px(p[2]), py(p[3]))
    ..lineTo(px(p[4]), py(p[5]))
    ..close();

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(k);

    // 机身 + 两道内斜面。
    _poly(
      canvas,
      _octagon(188, 92, 444, 227, 16),
      fill: _bg,
      stroke: _edge1,
      w: 1.4,
    );
    _poly(canvas, _octagon(193, 97, 439, 222, 13), stroke: _edge2, w: 1.0);
    _poly(canvas, _octagon(198, 102, 434, 217, 11), stroke: _edge3, w: 1.0);

    // 中间小屏。
    final screen = RRect.fromRectAndRadius(
      Rect.fromLTWH(px(284), py(112), (348 - 284) * 1.5, (143 - 112) * 1.5),
      const Radius.circular(4),
    );
    canvas.drawRRect(screen, Paint()..color = _bg);
    canvas.drawRRect(
      screen,
      Paint()
        ..color = _art
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2,
    );

    // 十字键：外圈 + 内圈，再补中心两道圆环。
    final cx = px(246), cy = py(165);
    _poly(canvas, _cross(246, 165, 38, 10), fill: _bg, stroke: _art, w: 1.3);
    _poly(canvas, _cross(246, 165, 35.5, 7.5), stroke: _art2, w: 1.0);
    canvas.drawCircle(Offset(cx, cy), 10 * 1.5, Paint()..color = _bg);
    canvas.drawCircle(
      Offset(cx, cy),
      10 * 1.5,
      Paint()
        ..color = _art
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2,
    );
    canvas.drawCircle(
      Offset(cx, cy),
      6.5 * 1.5,
      Paint()
        ..color = _art2
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.0,
    );

    // 四个方向三角。
    for (final t in <List<double>>[
      [246, 132, 241, 140, 251, 140],
      [246, 198, 241, 190, 251, 190],
      [216, 165, 224, 160, 224, 170],
      [276, 165, 268, 160, 268, 170],
    ]) {
      _poly(canvas, _tri(t), fill: _bg, stroke: _glyph, w: 1.1);
    }

    // 四枚面键。
    for (final f in <List<double>>[
      [386, 141],
      [361, 165],
      [411, 165],
      [386, 189],
    ]) {
      final c = Offset(px(f[0]), py(f[1]));
      canvas.drawCircle(c, 13 * 1.5, Paint()..color = _bg);
      canvas.drawCircle(
        c,
        13 * 1.5,
        Paint()
          ..color = _art
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2,
      );
      canvas.drawCircle(
        c,
        9.5 * 1.5,
        Paint()
          ..color = _art2
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2,
      );
    }

    // 三枚小键：SEL_1 左三角、HOME 方、SEL_2 右三角（外圈粗、内圈细，双线描边）。
    for (final t in <List<double>>[
      [282, 202, 296, 194, 296, 210],
      [350, 202, 336, 194, 336, 210],
    ]) {
      final p = _tri(t);
      _poly(canvas, p, fill: _bg, stroke: _art, w: 1.2);
      _poly(canvas, p, stroke: _art2, w: 0.9);
    }
    final home = Rect.fromLTWH(
      px(309),
      py(194),
      (323 - 309) * 1.5,
      (210 - 194) * 1.5,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(home, const Radius.circular(3)),
      Paint()..color = _bg,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(home, const Radius.circular(3)),
      Paint()
        ..color = _art
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2,
    );

    // 引线：先从按钮框的文字末端沿**标签自己的高度**水平走一段，再按 45° 斜到
    // 键边上那颗点。反过来先斜再平的话，好几条线会在键那一侧共用同一段水平线
    // ——用户看到的「引导线重叠」就是这么来的；标签高度各不相同，水平段因此永不重
    // 合，与官方图的走法一致。
    final lead = Paint()
      ..color = this.lead
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6
      ..strokeCap = StrokeCap.round;
    final dot = Paint()..color = this.dot;
    _anchors.forEach((bit, a) {
      final r = boxes[bit];
      if (r == null) return;
      final row = _rows[bit]!;
      // 终点落在键朝标签那一侧的边缘上（官方图如此）：压在字母上就认不出是哪颗键了。
      final off = _leadOffset[bit] ?? const [0.0, 0.0];
      final to = Offset(px(a[0] + off[0]), py(a[1] + off[1]));
      // 引线从框**朝键那一侧**的边出发：锚边只管排版，键在左边就得从左边出线，
      // 否则线会横穿整个文字框。
      final start = row.center != null
          ? Offset(r.center.dx, r.top - 6)
          : Offset(
              to.dx >= r.center.dx ? r.right + 6 : r.left - 6,
              r.center.dy,
            );
      final dir = to.dx >= start.dx ? 1 : -1;
      // 45° 段要正好吃掉竖直方向的差，剩下的交给水平段。
      final knee = BindingPad.leadKnee(start, to);
      if ((knee.dx - start.dx) * dir < 0) {
        // 标签贴得太近，折点落到起点之后：只画一条斜线，别画出回头线。
        canvas.drawLine(start, to, lead);
      } else {
        canvas.drawLine(start, knee, lead);
        canvas.drawLine(knee, to, lead);
      }
      canvas.drawCircle(to, 2.9, dot);
    });

    // 面键字母（16 px 半粗，圆心左上偏 14 / 12）。
    _letters.forEach((bit, letter) {
      final a = _anchors[bit]!;
      final tp = TextPainter(
        text: TextSpan(
          text: letter,
          style: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            color: _glyph,
            height: 1.0,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(
        canvas,
        Offset(px(a[0]) - 14 - (tp.width - 28) / 2, py(a[1]) - 12),
      );
    });

    // 按下发光画在最上层。
    for (final bit in pressed) {
      final a = _anchors[bit]; // 圆心：发光要盖住整颗键，不用让字母
      final rad = _glowR[bit];
      if (a == null || rad == null) continue;
      final c = Offset(px(a[0]), py(a[1]));
      canvas.drawCircle(c, rad * 1.5, Paint()..color = _glowFill);
      canvas.drawCircle(
        c,
        rad * 1.5,
        Paint()
          ..color = _glowStroke
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.4,
      );
    }
  }

  @override
  bool shouldRepaint(_PadPainter old) =>
      old.k != k ||
      old.pressed != pressed ||
      old.warning != warning ||
      old.lead != lead ||
      old.dot != dot;
}
