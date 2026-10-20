import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../app/tokens.dart';

/// 一张采样：相对现在的时间（秒，负数表示过去）+ A / B 两通道的分段强度。
typedef WaveSample = ({double at, List<int> a, List<int> b});

/// 一条气压采样：相对现在的时间与 kPa 值。
typedef PressureSample = ({double at, double kpa});

/// 实时输出波形：A / B 各一栏，每帧采样按四个子段各画一根柱，柱高就是该子段的
/// 瞬时强度（0-100），最右侧为最新一帧。
///
/// 引擎每帧下发的是**四个子段**的强度，
/// 逐段画出来才看得见波形起伏；压成「这一格有没有信号」就只剩一根方波，用户读
/// 作「波形图根本没在跟数据」。外观仍是本应用的 TickStrip（等距圆角短条），
/// 但高度完全由数据决定，不用正弦伪造起伏。
class WaveChart extends StatelessWidget {
  const WaveChart({
    super.key,
    required this.samples,
    this.windowSeconds = 5.0,
    this.height = 104,
  });

  final List<WaveSample> samples;
  final double windowSeconds;
  final double height;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _WavePainter(
          samples: samples,
          window: windowSeconds,
          laneA: t.accent,
          laneB: t.isDark ? const Color(0xFF2DD4BF) : const Color(0xFF0E8C7F),
          grid: t.hairline,
          text: t.textTertiary,
          idle: t.textTertiary,
        ),
      ),
    );
  }
}

class _WavePainter extends CustomPainter {
  _WavePainter({
    required this.samples,
    required this.window,
    required this.laneA,
    required this.laneB,
    required this.grid,
    required this.text,
    required this.idle,
  });

  final List<WaveSample> samples;
  final double window;
  final Color laneA;
  final Color laneB;
  final Color grid;
  final Color text;
  final Color idle;

  /// 引擎每帧固定下发四个子段（`ble.py` 里 `[0,0,0,0]` 填强度）。
  static const _segs = 4;

  /// 一个协议子段有多长：设备一帧 0.1 s、四个子段（`dglab/waves.py` 的 8 字节帧），
  /// 所以画得再细也就到 25 ms 一格。
  static const _segSeconds = 0.1 / _segs;

  @override
  void paint(Canvas canvas, Size size) {
    const labelH = 12.0;
    const gap = 8.0;
    const gutter = 58.0;
    final laneH = math.max(10.0, (size.height - gap) / 2 - labelH);
    final plotW = math.max(40.0, size.width - gutter);
    // 柱子落在**时间轴**上。以前按「这一窗里有几帧」把样本量化成 4..72 个格位：
    // v4 一次批 10 帧、A/B 两条通道交错，相邻样本挤进同一格，后画的把先画的盖掉，
    // 用户看到的「吞波形」就是被量化丢掉的那几帧。
    final segW = plotW * (_segSeconds / window);
    final tickW = math.max(1.2, segW * .72);

    for (var lane = 0; lane < 2; lane++) {
      final top = lane * (labelH + laneH + gap);
      final color = lane == 0 ? laneA : laneB;
      final base = top + labelH + laneH;
      canvas.drawLine(
        Offset(0, base - .5),
        Offset(plotW, base - .5),
        Paint()..color = grid,
      );

      // 底噪格：没信号时也要读得出「通道在待命」。格子从最右端往左铺，
      // 数据柱也是按「离最右端多远」落位的，两套共用同一条时间刻度。
      final idlePaint = Paint()..color = idle.withAlpha(idle.a > .5 ? 60 : 26);
      for (var x = plotW - segW; x > -segW; x -= segW) {
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(
              x + (segW - tickW) / 2,
              base - laneH * .16,
              tickW,
              laneH * .16,
            ),
            const Radius.circular(1.2),
          ),
          idlePaint,
        );
      }

      var peak = 0;
      for (final s in samples) {
        // 时间戳在 0 右边的是「已经排进下一秒、设备还没播」的帧，画出来会让波形
        // 整体往左挤；窗口左边以外的老帧不再画。
        if (s.at > 0 || s.at < -window - _segSeconds) continue;
        final segs = lane == 0 ? s.a : s.b;
        if (segs.isEmpty) continue;
        for (final v in segs) {
          if (v > peak) peak = v;
        }
        // 这一帧的右边缘：at = 0 贴着最右，at = -window 贴着最左。
        final left = plotW * (1.0 + s.at / window) - segs.length * segW;
        for (var i = 0; i < segs.length; i++) {
          final v = (segs[i] / 100).clamp(0.0, 1.0);
          if (v <= 0) continue;
          final h = laneH * (.18 + .82 * v);
          canvas.drawRRect(
            RRect.fromRectAndRadius(
              Rect.fromLTWH(
                left + i * segW + (segW - tickW) / 2,
                base - h,
                tickW,
                h,
              ),
              const Radius.circular(1.2),
            ),
            Paint()..color = color.withAlpha((90 + 165 * v).round()),
          );
        }
      }

      _label(canvas, lane == 0 ? 'A' : 'B', Offset(2, top), color, bold: true);
      _label(canvas, 'STR 0-100', Offset(14, top), text);
      // 读数报窗口里的子段峰值：报「最后一格」会在脉冲间隙显示成 0，看着像没在跟数据。
      if (peak > 0) {
        _label(
          canvas,
          '峰值 $peak',
          Offset(size.width, top),
          text,
          right: size.width,
        );
      }
    }
  }

  void _label(
    Canvas canvas,
    String text,
    Offset at,
    Color color, {
    bool bold = false,
    double? right,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          fontFamily: 'Consolas',
          fontSize: 9,
          height: 1,
          fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
          color: color,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(
      canvas,
      Offset(right == null ? at.dx : right - painter.width, at.dy),
    );
  }

  @override
  bool shouldRepaint(_WavePainter old) =>
      old.samples != samples || old.laneA != laneA || old.laneB != laneB;
}

/// 气压曲线：最近 60 秒、0-60 kPa，横轴标 -60 s → 现在。
class PressureChart extends StatelessWidget {
  const PressureChart({
    super.key,
    required this.samples,
    required this.legend,
    this.windowSeconds = 60,
    this.maxKpa = 60,
    this.height = 176,
  });

  final List<PressureSample> samples;
  final String legend;
  final double windowSeconds;
  final double maxKpa;
  final double height;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _PressurePainter(
          samples: samples,
          window: windowSeconds,
          maxKpa: maxKpa,
          legend: legend,
          plot: t.wellDeep,
          grid: t.hairline,
          curve: t.portModel,
          text: t.textTertiary,
        ),
      ),
    );
  }
}

class _PressurePainter extends CustomPainter {
  _PressurePainter({
    required this.samples,
    required this.window,
    required this.maxKpa,
    required this.legend,
    required this.plot,
    required this.grid,
    required this.curve,
    required this.text,
  });

  final List<PressureSample> samples;
  final double window;
  final double maxKpa;
  final String legend;
  final Color plot;
  final Color grid;
  final Color curve;
  final Color text;

  static const _leftPad = 30.0;
  static const _bottomPad = 16.0;

  @override
  void paint(Canvas canvas, Size size) {
    final area = Rect.fromLTWH(
      _leftPad,
      6,
      size.width - _leftPad - 6,
      size.height - 6 - _bottomPad,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(area, const Radius.circular(6)),
      Paint()..color = plot,
    );

    final line = Paint()
      ..color = grid
      ..strokeWidth = 1;
    // 网格按四等分：0 / 15 / 30 / 45 / 60 kPa。
    for (var i = 0; i <= 4; i++) {
      final y = area.bottom - area.height * i / 4;
      canvas.drawLine(Offset(area.left, y), Offset(area.right, y), line);
      _label(
        canvas,
        '${(maxKpa * i / 4).round()}',
        Offset(2, y - 4),
        text,
        right: area.left - 4,
      );
    }
    _label(canvas, 'kPa', Offset(area.left + 2, area.top + 2), text);
    _label(
      canvas,
      '-${window.round()} s',
      Offset(area.left, area.bottom + 3),
      text,
    );
    _label(canvas, '现在', Offset(area.right - 24, area.bottom + 3), text);

    if (samples.length < 2) {
      _label(
        canvas,
        '等待气压采样…',
        Offset(area.center.dx - 34, area.center.dy - 5),
        text,
      );
      return;
    }

    final path = Path();
    var started = false;
    for (final s in samples) {
      if (s.at < -window) continue;
      final x = area.right - (-s.at / window) * area.width;
      final y =
          area.bottom -
          (math.min(maxKpa, math.max(0, s.kpa)) / maxKpa) * area.height;
      if (!started) {
        path.moveTo(x, y);
        started = true;
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = curve
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.6
        ..strokeJoin = StrokeJoin.round,
    );

    final dot = Paint()..color = curve;
    final last = samples.last;
    canvas.drawCircle(
      Offset(
        area.right,
        area.bottom - (math.min(maxKpa, last.kpa) / maxKpa) * area.height,
      ),
      2.6,
      dot,
    );
    _label(canvas, legend, Offset(area.right - 96, area.top + 3), curve);
  }

  void _label(
    Canvas canvas,
    String text,
    Offset at,
    Color color, {
    double? right,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          fontFamily: 'Consolas',
          fontSize: 9,
          height: 1,
          color: color,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final x = right == null ? at.dx : right - painter.width;
    painter.paint(canvas, Offset(x, at.dy));
  }

  @override
  bool shouldRepaint(_PressurePainter old) =>
      old.samples != samples || old.legend != legend || old.curve != curve;
}
