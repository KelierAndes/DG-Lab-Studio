import 'dart:math' as math;

import 'package:flutter/material.dart' hide Stack;

import '../app/field_metrics.dart';
import '../app/tokens.dart';
import '../pages/app_pages.dart';
import 'nav_rail.dart';
import 'primitives.dart';
import 'scroll_guard.dart';

/// 页面里的一组：一行小标题（可带一句说明），下面一块凹陷面板。
///
/// 控制页 / 连接页按新 UI 语言分组时用——把散平的表单变成有层级的几块，
/// 而不是把旧界面那张表单原样搬过来。
class GroupPanel extends StatelessWidget {
  const GroupPanel({
    super.key,
    required this.label,
    this.hint,
    this.trailing,
    required this.child,
  });

  final String label;
  final String? hint;
  final Widget? trailing;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              SectionLabel(label),
              if (hint case final why?) ...[
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    why,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9,
                      height: 1.2,
                      color: t.textTertiary,
                    ),
                  ),
                ),
              ] else
                const Spacer(),
              if (trailing case final tail?)
                Padding(padding: const EdgeInsets.only(bottom: 1), child: tail),
            ],
          ),
          const SizedBox(height: 5),
          WellBox(
            padding: const EdgeInsets.fromLTRB(10, 9, 10, 9),
            child: child,
          ),
        ],
      ),
    );
  }
}

/// One bento tile: the same material as a node card — raised surface, "● Title"
/// header, and the content sunk into a well.
class BentoCard extends StatelessWidget {
  const BentoCard({
    super.key,
    required this.title,
    required this.child,
    this.trailing,
    this.accent,
    this.dense = false,
    this.flat = false,
    this.fit = false,
  });

  final String title;
  final Widget child;
  final Widget? trailing;

  /// Colour of the header bullet, which is how the tiles tell each other apart
  /// without reaching for a saturated title bar.
  final Color? accent;

  /// Tighter padding for tiles that only hold a single line.
  final bool dense;

  /// Skip the inner well: used by tiles that draw their own surface.
  final bool flat;

  /// 内容有多少高度就占多少：卡片不再撑固定高度，里面也不必再套滚动条。
  /// 用这一档时子树里不能有 Expanded / ListView（父级是无界高度）。
  final bool fit;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return IsolatedScroll(
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: t.surface,
          borderRadius: BorderRadius.circular(AppTokens.radiusPanel - 2),
          border: Border.all(color: t.hairline),
          boxShadow: t.raised(),
        ),
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            dense ? 12 : 14,
            dense ? 10 : 12,
            dense ? 12 : 14,
            dense ? 10 : 12,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                height: 20,
                child: Row(
                  children: [
                    Dot(color: accent ?? t.textSecondary, size: 4.5),
                    const SizedBox(width: 7),
                    Expanded(
                      child: Text(
                        title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 12,
                          height: 1,
                          letterSpacing: .1,
                          color: t.textSecondary,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    ?trailing,
                  ],
                ),
              ),
              const SizedBox(height: 9),
              if (fit)
                SizedBox(
                  width: double.infinity,
                  child: flat ? child : WellBox(child: child),
                )
              else
                Expanded(child: flat ? child : WellBox(child: child)),
            ],
          ),
        ),
      ),
    );
  }
}

/// A page's own header row: title, an optional search well, and actions.
class PageHeader extends StatelessWidget {
  const PageHeader({
    super.key,
    required this.title,
    required this.subtitle,
    this.search = true,
    this.actions = const [],
    required this.pages,
  });

  final String title;
  final String subtitle;
  final bool search;
  final List<Widget> actions;
  final AppNav pages;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: TextStyle(
                  fontSize: 19,
                  height: 1.1,
                  letterSpacing: -.1,
                  fontWeight: FontWeight.w600,
                  color: t.textPrimary,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                subtitle,
                style: TextStyle(
                  fontSize: 11.5,
                  height: 1,
                  color: t.textTertiary,
                  letterSpacing: .1,
                ),
              ),
            ],
          ),
        ),
        if (search) ...[
          SizedBox(width: 232, child: SearchWell(pages: pages)),
          const SizedBox(width: 9),
        ],
        ...actions,
      ],
    );
  }
}

class SearchWell extends StatefulWidget {
  const SearchWell({super.key, required this.pages, this.hint = '搜索'});

  final AppNav pages;
  final String hint;

  @override
  State<SearchWell> createState() => _SearchWellState();
}

class _SearchWellState extends State<SearchWell> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.pages.query,
  );

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return SizedBox(
      height: 32,
      child: WellBox(
        child: Row(
          children: [
            const SizedBox(width: 9),
            Icon(Icons.search_rounded, size: 14, color: t.textTertiary),
            const SizedBox(width: 7),
            Expanded(
              child: singleLineBox(
                TextField(
                  controller: _ctl,
                  onChanged: widget.pages.search,
                  textAlignVertical: TextAlignVertical.center,
                  style: fieldTextStyle(t.textPrimary),
                  strutStyle: fieldStrut(fontSize: 11.5),
                  cursorColor: t.accent,
                  cursorWidth: 1.2,
                  decoration: InputDecoration(
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                    border: InputBorder.none,
                    hintText: widget.hint,
                    hintStyle: fieldHintStyle(t.textTertiary),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 6),
          ],
        ),
      ),
    );
  }
}

/// Raised option pills; the active one inverts to the same white the rail and
/// the Share button use.
class SegmentedPills extends StatelessWidget {
  const SegmentedPills({
    super.key,
    required this.options,
    required this.index,
    required this.onPick,
    this.compact = false,
  });

  final List<String> options;
  final int index;
  final ValueChanged<int> onPick;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < options.length; i++) ...[
          if (i > 0) const SizedBox(width: 5),
          OptionPill(
            label: options[i],
            active: i == index,
            dense: compact,
            onTap: () => onPick(i),
          ),
        ],
      ],
    );
  }
}

class OptionPill extends StatelessWidget {
  const OptionPill({
    super.key,
    required this.label,
    required this.active,
    required this.onTap,
    this.dense = false,
    this.height,
    this.fontSize,
  });

  final String label;
  final bool active;
  final bool dense;
  final VoidCallback onTap;

  /// 顶栏那排要跟窗口按钮同高，光靠 padding 撑不出确定的高度。
  final double? height;
  final double? fontSize;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 110),
          height: height,
          alignment: height == null ? null : Alignment.center,
          padding: EdgeInsets.symmetric(
            horizontal: dense ? 9 : 11,
            vertical: dense ? 4.5 : 5.5,
          ),
          decoration: BoxDecoration(
            // The active pill inverts to ink on a light theme, the same way the
            // Share button does, so it stays the loudest thing either way.
            color: active ? (t.isDark ? Colors.white : t.textPrimary) : t.pill,
            borderRadius: BorderRadius.circular(AppTokens.radiusPill),
            border: Border.all(
              color: active
                  ? (t.isDark ? Colors.white : t.textPrimary)
                  : t.pillBorder,
            ),
            boxShadow: active
                ? [
                    BoxShadow(
                      color: Colors.black.withAlpha(
                        (t.shadowAlpha * 110).round(),
                      ),
                      blurRadius: 8,
                      offset: const Offset(0, 3),
                    ),
                  ]
                : t.raised(depth: .35),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: fontSize ?? (dense ? 9.5 : 10.5),
              height: 1.15,
              letterSpacing: .1,
              fontWeight: active ? FontWeight.w600 : FontWeight.w500,
              color: active
                  ? (t.isDark ? t.wellDeep : Colors.white)
                  : t.textSecondary,
            ),
          ),
        ),
      ),
    );
  }
}

/// 急停：红底白字，颜色写死不随明暗主题变——每页右上角都只放这一颗，一眼要认得出。
class EstopButton extends StatefulWidget {
  const EstopButton({super.key, this.label = '急停全部设备', required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  State<EstopButton> createState() => _EstopButtonState();
}

class _EstopButtonState extends State<EstopButton> {
  bool _hot = false;
  bool _down = false;

  @override
  Widget build(BuildContext context) {
    const idle = Color(0xFFCE3A3A);
    final bg = _down
        ? const Color(0xFFA82D2D)
        : (_hot ? const Color(0xFFE14F4F) : idle);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: widget.onTap,
      onTapDown: (_) => setState(() => _down = true),
      onTapUp: (_) => setState(() => _down = false),
      onTapCancel: () => setState(() => _down = false),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hot = true),
        onExit: (_) => setState(() => _hot = false),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 110),
          height: 30,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(AppTokens.radiusPill),
            border: Border.all(color: bg),
            boxShadow: const [
              BoxShadow(
                color: Color(0x59000000),
                blurRadius: 9,
                offset: Offset(0, 3),
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.dangerous_outlined,
                size: 14,
                color: Colors.white,
              ),
              const SizedBox(width: 6),
              Text(
                widget.label,
                style: const TextStyle(
                  fontSize: 11,
                  height: 1.15,
                  letterSpacing: .1,
                  fontWeight: FontWeight.w700,
                  color: Colors.white,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The round day selectors under the chart.
class DayDial extends StatelessWidget {
  const DayDial({
    super.key,
    required this.count,
    required this.index,
    required this.label,
    required this.onPick,
  });

  final int count;
  final int index;
  final String Function(int) label;
  final ValueChanged<int> onPick;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        for (var i = 0; i < count; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => onPick(i),
            child: MouseRegion(
              cursor: SystemMouseCursors.click,
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 110),
                width: 22,
                height: 22,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: i == index ? t.textSecondary : Colors.transparent,
                  border: Border.all(
                    color: i == index ? t.textSecondary : t.pillBorder,
                  ),
                ),
                child: Text(
                  label(i),
                  style: TextStyle(
                    fontSize: 9.5,
                    height: 1,
                    color: i == index ? t.surface : t.textTertiary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
          ),
        ],
      ],
    );
  }
}

/// Rounded bars with a value bubble over the highlighted one, plus the tick
/// strip the reference uses for its progress tile.
class BarChart extends StatelessWidget {
  const BarChart({
    super.key,
    required this.values,
    required this.highlight,
    this.bubble = '',
    this.tint,
  });

  final List<int> values;
  final int highlight;
  final String bubble;
  final Color? tint;

  @override
  Widget build(BuildContext context) => CustomPaint(
    size: Size.infinite,
    painter: BarChartPainter(
      values: values,
      highlight: highlight,
      bubble: bubble,
      tokens: AppScope.of(context),
      tint: tint,
    ),
  );
}

class BarChartPainter extends CustomPainter {
  const BarChartPainter({
    required this.values,
    required this.highlight,
    required this.bubble,
    required this.tokens,
    this.tint,
  });

  final List<int> values;
  final int highlight;
  final String bubble;
  final AppTokens tokens;
  final Color? tint;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.isEmpty || size.width <= 0 || size.height <= 0) return;
    final t = tokens;
    final maxV = math.max(1, values.reduce((a, b) => a > b ? a : b));
    final slot = size.width / values.length;
    final w = math.min(14.0, slot * .46);
    final accent = tint ?? t.accent;

    for (var i = 0; i < values.length; i++) {
      final h = math.max(4.0, values[i] / maxV * (size.height - 16));
      final cx = slot * (i + .5);
      final bar = Rect.fromLTWH(cx - w / 2, size.height - h, w, h);
      final on = i == highlight;
      canvas.drawRRect(
        RRect.fromRectAndRadius(bar, Radius.circular(w / 2)),
        Paint()
          ..shader = LinearGradient(
            begin: Alignment.bottomCenter,
            end: Alignment.topCenter,
            colors: on
                ? [accent.withAlpha(t.isDark ? 120 : 90), accent]
                : [
                    t.wellDeep,
                    t.isDark
                        ? const Color(0xFF3C3C46)
                        : const Color(0xFFBFC1CB),
                  ],
          ).createShader(bar),
      );
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(bar.left, bar.top, bar.width, math.min(3, bar.height)),
          Radius.circular(w / 2),
        ),
        Paint()..color = Colors.white.withAlpha(on ? 90 : 26),
      );
    }

    if (bubble.isEmpty) return;
    final style = TextStyle(
      fontSize: 10,
      height: 1,
      color: t.textOnAccent,
      fontWeight: FontWeight.w600,
      letterSpacing: .1,
    );
    final tp = TextPainter(
      text: TextSpan(text: bubble, style: style),
      textDirection: TextDirection.ltr,
    )..layout();
    final bx = slot * (highlight + .5);
    final top =
        size.height -
        math.max(4.0, values[highlight] / maxV * (size.height - 16));
    final box = Rect.fromLTWH(
      bx - tp.width / 2 - 7,
      math.max(0, top - 22),
      tp.width + 14,
      16,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(box, const Radius.circular(8)),
      Paint()..color = t.accent,
    );
    tp.paint(canvas, Offset(box.left + 7, box.top + 3.5));
  }

  @override
  bool shouldRepaint(BarChartPainter old) =>
      old.values != values ||
      old.highlight != highlight ||
      old.tokens != tokens ||
      old.bubble != bubble;
}

/// Thin vertical ticks, dense on the left and thinning out — the reference's
/// progress footer.
class TickStrip extends StatelessWidget {
  const TickStrip({super.key, required this.ratio, this.tint, this.count = 34});

  /// 0..1 of the ticks that are lit.
  final double ratio;
  final Color? tint;
  final int count;

  @override
  Widget build(BuildContext context) => CustomPaint(
    size: Size.infinite,
    painter: _TickPainter(
      ratio: ratio,
      tokens: AppScope.of(context),
      tint: tint,
      count: count,
    ),
  );
}

class _TickPainter extends CustomPainter {
  const _TickPainter({
    required this.ratio,
    required this.tokens,
    required this.tint,
    required this.count,
  });

  final double ratio;
  final AppTokens tokens;
  final Color? tint;
  final int count;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.width <= 0) return;
    final t = tokens;
    final slot = size.width / count;
    final lit = (count * ratio.clamp(0.0, 1.0)).round();
    for (var i = 0; i < count; i++) {
      final h =
          size.height * (i < lit ? .55 + .45 * math.sin(i / 2.4).abs() : .34);
      final rect = Rect.fromLTWH(
        slot * i + slot * .3,
        size.height - h,
        math.max(1, slot * .38),
        h,
      );
      canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(1)),
        Paint()
          ..color = i < lit
              ? (tint ?? t.accent).withAlpha(t.isDark ? 210 : 170)
              : t.textTertiary.withAlpha(t.isDark ? 70 : 60),
      );
    }
  }

  @override
  bool shouldRepaint(_TickPainter old) =>
      old.ratio != ratio || old.tokens != tokens;
}

/// Three numbers side by side, split by carved grooves.
class StatTrio extends StatelessWidget {
  const StatTrio({super.key, required this.items, this.tint});

  final List<({String label, String value})> items;
  final Color? tint;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        for (var i = 0; i < items.length; i++) ...[
          if (i > 0) Container(width: 1, height: 30, color: t.hairline),
          Expanded(
            child: Padding(
              padding: EdgeInsets.symmetric(horizontal: i == 0 ? 2 : 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TinyLabel(items[i].label, color: t.textTertiary, size: 8.5),
                  const SizedBox(height: 5),
                  Text(
                    items[i].value,
                    style: TextStyle(
                      fontSize: 19,
                      height: 1,
                      letterSpacing: -.3,
                      fontWeight: FontWeight.w600,
                      color: i == 0 ? (tint ?? t.textPrimary) : t.textPrimary,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }
}

/// Drawn monogram avatar: a two-tone disc with the initials on top.
class AvatarMark extends StatelessWidget {
  const AvatarMark({
    super.key,
    required this.name,
    this.seed = const [],
    this.size = 30,
  });

  final String name;
  final List<Color> seed;
  final double size;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final pair = seed.length >= 2
        ? seed
        : const [Color(0xFF7B61FF), Color(0xFF22D3EE)];
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [pair[0], pair[1]],
        ),
        border: Border.all(color: Colors.white.withAlpha(t.isDark ? 40 : 150)),
        boxShadow: t.raised(depth: .5),
      ),
      child: Text(
        initials(name),
        style: TextStyle(
          fontSize: size * .38,
          height: 1,
          color: Colors.white,
          fontWeight: FontWeight.w700,
          letterSpacing: .2,
        ),
      ),
    );
  }

  static String initials(String name) {
    final parts = name
        .trim()
        .split(RegExp(r'[\s._]+'))
        .where((p) => p.isNotEmpty)
        .toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) {
      return parts.first.characters.take(2).toString().toUpperCase();
    }
    return (parts.first[0] + parts.last[0]).toUpperCase();
  }
}

/// Small status pill; `tone` picks accent green, amber or neutral.
class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.text, this.tone = 0});

  final String text;

  /// 0 neutral · 1 accent · 2 amber · 3 blue
  final int tone;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final color = switch (tone) {
      1 => t.accent,
      2 => const Color(0xFFF5C542),
      3 => t.portImage,
      _ => t.textTertiary,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: color.withAlpha(t.isDark ? 44 : 34),
        borderRadius: BorderRadius.circular(AppTokens.radiusPill - 2),
        border: Border.all(color: color.withAlpha(120)),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 9.5,
          height: 1.1,
          letterSpacing: .3,
          color: color,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// A tile row: leading mark, two lines of text, trailing slot.
class TileRow extends StatelessWidget {
  const TileRow({
    super.key,
    required this.title,
    required this.subtitle,
    required this.leading,
    this.trailing,
    this.onTap,
    this.selected = false,
    this.detail,
  });

  final String title;
  final String subtitle;
  final Widget leading;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool selected;

  /// Extra copy revealed under the row when it is the selected one.
  final Widget? detail;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final body = Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 8),
      decoration: BoxDecoration(
        color: selected
            ? t.surfaceRaised.withAlpha(t.isDark ? 150 : 200)
            : Colors.transparent,
        borderRadius: BorderRadius.circular(AppTokens.radiusField),
        border: Border.all(color: selected ? t.pillBorder : Colors.transparent),
      ),
      child: Row(
        children: [
          leading,
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11.5,
                    height: 1.2,
                    color: t.textSecondary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 10,
                    height: 1.1,
                    color: t.textTertiary,
                  ),
                ),
              ],
            ),
          ),
          ?trailing,
        ],
      ),
    );
    return Column(
      children: [
        if (onTap == null) body else InkWell(onTap: onTap, child: body),
        if (selected && detail != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(9, 4, 9, 2),
            child: detail!,
          ),
      ],
    );
  }
}

/// A thumbnailed row lead: the reference's 32px rounded icon slots.
class ThumbMark extends StatelessWidget {
  const ThumbMark({super.key, this.image, this.size = 32, this.seed});

  final String? image;
  final double size;
  final Color? seed;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: t.wellDeep,
        borderRadius: BorderRadius.circular(AppTokens.radiusField - 1),
        border: Border.all(color: Colors.black.withAlpha(t.isDark ? 130 : 40)),
        boxShadow: t.inset,
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppTokens.radiusField - 2),
        child: image == null
            ? DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [
                      (seed ?? t.accent).withAlpha(190),
                      (seed ?? t.accent).withAlpha(60),
                    ],
                  ),
                ),
              )
            : Image.asset(image!, fit: BoxFit.cover),
      ),
    );
  }
}

/// Section heading inside a page, with an optional right-aligned action.
class SectionHead extends StatelessWidget {
  const SectionHead(this.title, {super.key, this.action});

  final String title;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        Expanded(
          child: Text(
            title,
            style: TextStyle(
              fontSize: 12.5,
              height: 1,
              letterSpacing: .1,
              fontWeight: FontWeight.w600,
              color: t.textSecondary,
            ),
          ),
        ),
        ?action,
      ],
    );
  }
}

/// The page body: a scrollable bento column that splits into two unequal
/// columns the way the reference does, and stacks when the window is narrow.
class BentoLayout extends StatelessWidget {
  const BentoLayout({
    super.key,
    required this.left,
    required this.right,
    this.gap = 12,
    this.stretch = false,
  });

  final List<Widget> left;
  final List<Widget> right;
  final double gap;

  /// 两栏同高：父级给 IntrinsicHeight，短的那栏补齐外框，内容仍然顶格不自铺。
  final bool stretch;

  @override
  Widget build(BuildContext context) {
    final narrow = MediaQuery.sizeOf(context).width < 900;
    if (narrow) {
      return Column(children: [...left, ...right]);
    }
    // stretch 时子卡要显式吃满这一栏：Column 的普通子项只按自身高度排，
    // 不包 Expanded 的话两栏外框还是对不齐。
    List<Widget> side(List<Widget> items) => [
      for (final w in items) Expanded(child: w),
    ];
    return Row(
      crossAxisAlignment: stretch
          ? CrossAxisAlignment.stretch
          : CrossAxisAlignment.start,
      children: [
        Expanded(
          flex: 62,
          child: Column(children: stretch ? side(left) : left),
        ),
        SizedBox(width: gap),
        Expanded(
          flex: 38,
          child: Column(children: stretch ? side(right) : right),
        ),
      ],
    );
  }
}

/// Wraps a page: fixed header, then a scrolling bento body.
class PageBody extends StatelessWidget {
  const PageBody({
    super.key,
    required this.pages,
    required this.header,
    required this.children,
  });

  final AppNav pages;
  final Widget header;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: pages,
      builder: (context, _) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 18),
        child: Column(
          children: [
            header,
            const SizedBox(height: 14),
            Expanded(
              child: SingleChildScrollView(child: Column(children: children)),
            ),
          ],
        ),
      ),
    );
  }
}
