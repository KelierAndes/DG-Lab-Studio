import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app/field_metrics.dart';
import '../app/tokens.dart';

// ------------------------------------------------------------------ popover

class MenuEntry {
  const MenuEntry(
    this.label, {
    this.selected = false,
    this.color,
    this.hint,
    this.danger = false,
  });

  final String label;
  final bool selected;
  final Color? color;
  final String? hint;
  final bool danger;
}

class PopoverSpec {
  PopoverSpec({
    this.anchor,
    this.anchorRect,
    required this.entries,
    required this.onPick,
    this.width = 190,
    this.title,
  }) : assert(
         anchor != null || anchorRect != null,
         'a menu needs either a cursor point or the control it belongs to',
       );

  /// A cursor point. Only the canvas uses this: there the menu *is* the thing
  /// that creates a node, so it has to open where the pointer was.
  final Offset? anchor;

  /// The control's own box. Every dropdown field anchors here, so the list
  /// opens against the box it belongs to no matter where the pointer landed
  /// inside it.
  final Rect? anchorRect;

  final List<MenuEntry> entries;
  final ValueChanged<int> onPick;
  final double width;
  final String? title;
}

/// The painted box of the nearest render object, in root coordinates.
///
/// Both corners have to be mapped: a node card sits inside the canvas's scale
/// transform, and shifting an unscaled `size` onto a global origin would put
/// the box out by however much the view is zoomed.
Rect globalRect(BuildContext context) {
  final box = context.findRenderObject() as RenderBox?;
  if (box == null || !box.hasSize) return Rect.zero;
  return Rect.fromPoints(
    box.localToGlobal(Offset.zero),
    box.localToGlobal(box.size.bottomRight(Offset.zero)),
  );
}

class PopoverService extends ChangeNotifier {
  PopoverSpec? _spec;
  PopoverSpec? get spec => _spec;

  void show(PopoverSpec spec) {
    _spec = spec;
    notifyListeners();
  }

  void close() {
    if (_spec == null) return;
    _spec = null;
    notifyListeners();
  }

  bool get isOpen => _spec != null;
}

/// Floating glass menu. Pushed as a plain subtree of the shell's root Stack:
/// a Navigator route inside a frameless per-pixel-transparent window paints
/// but never receives pointer events, so routes are off the table.
class PopoverLayer extends StatelessWidget {
  const PopoverLayer({
    super.key,
    required this.service,
    required this.boundary,
  });

  final PopoverService service;
  final Size boundary;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: service,
      builder: (context, _) {
        final spec = service.spec;
        if (spec == null) return const SizedBox.shrink();
        return Positioned.fill(
          child: Stack(
            children: [
              // Transparent barrier: a click anywhere outside the menu closes
              // it. Deliberately not wrapped in an extra absorbing detector,
              // which would steal the menu's own taps.
              Positioned.fill(
                child: GestureDetector(
                  behavior: HitTestBehavior.translucent,
                  onTapDown: (_) => service.close(),
                  child: const SizedBox.expand(),
                ),
              ),
              _menu(context, spec),
            ],
          ),
        );
      },
    );
  }

  Widget _menu(BuildContext context, PopoverSpec spec) {
    final t = AppScope.of(context);
    final height =
        30.0 + (spec.title == null ? 0 : 20.0) + spec.entries.length * 30.0;
    double x, y;
    final rect = spec.anchorRect;
    if (rect != null) {
      // Flush with the control's left edge, opening downwards, and flip above
      // it when the bottom of the panel is too close.
      x = rect.left;
      y = rect.bottom + 6;
      if (y + height > boundary.height - 8) y = rect.top - height - 6;
    } else {
      // 光标处弹出的菜单：卡片左上角对齐指针，向下展开。以前是朝上开
      // （`a.dy - height - 6`），指针落在哪儿、菜单就跑到哪儿的上方，看上去
      // 就是「弹菜与光标差得远」。只有下方放不下时才向上翻一次。
      final a = spec.anchor!;
      x = a.dx;
      y = a.dy;
      if (y + height > boundary.height - 8) y = a.dy - height - 6;
    }
    x = x.clamp(8.0, math.max(8.0, boundary.width - spec.width - 8)).toDouble();
    y = y.clamp(8.0, math.max(8.0, boundary.height - height - 8)).toDouble();

    return Positioned(
      left: x,
      top: y,
      width: spec.width,
      height: height,
      child: GestureDetector(
        key: const ValueKey('popover-menu'),
        behavior: HitTestBehavior.opaque,
        onTapDown: (_) {},
        child: ClipRRect(
          borderRadius: BorderRadius.circular(11),
          child: BackdropFilter(
            filter: ui.ImageFilter.blur(sigmaX: 18, sigmaY: 18),
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: Color.alphaBlend(t.glassTint, t.surfaceRaised),
                borderRadius: BorderRadius.circular(11),
                border: Border.all(color: t.glassRim.withAlpha(90)),
                boxShadow: t.raised(depth: 1.35),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (spec.title != null)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(11, 9, 11, 3),
                      child: Text(
                        spec.title!.toUpperCase(),
                        style: TextStyle(
                          fontSize: 10,
                          letterSpacing: 1.2,
                          fontWeight: FontWeight.w600,
                          color: t.textTertiary,
                        ),
                      ),
                    ),
                  for (var i = 0; i < spec.entries.length; i++)
                    _MenuRow(
                      entry: spec.entries[i],
                      onTap: () {
                        // Close first: a handler may immediately open a
                        // replacement menu (the two-step node palette does).
                        service.close();
                        spec.onPick(i);
                      },
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _MenuRow extends StatefulWidget {
  const _MenuRow({required this.entry, required this.onTap});

  final MenuEntry entry;
  final VoidCallback onTap;

  @override
  State<_MenuRow> createState() => _MenuRowState();
}

class _MenuRowState extends State<_MenuRow> {
  bool _hot = false;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final e = widget.entry;
    return MouseRegion(
      onEnter: (_) => setState(() => _hot = true),
      onExit: (_) => setState(() => _hot = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        child: Container(
          height: 30,
          padding: const EdgeInsets.symmetric(horizontal: 11),
          decoration: BoxDecoration(
            color: _hot ? t.glassTint.withAlpha(38) : Colors.transparent,
            borderRadius: BorderRadius.circular(7),
          ),
          child: Row(
            children: [
              if (e.color != null)
                Container(
                  width: 6,
                  height: 6,
                  decoration: BoxDecoration(
                    color: e.color,
                    shape: BoxShape.circle,
                  ),
                )
              else if (e.selected)
                const SizedBox(width: 6)
              else
                const SizedBox(width: 0),
              if (e.color != null || e.selected) const SizedBox(width: 8),
              Expanded(
                child: Text(
                  e.label,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: e.selected ? FontWeight.w600 : FontWeight.w500,
                    color: e.danger
                        ? t.portNegative
                        : (e.selected ? t.textPrimary : t.textSecondary),
                  ),
                ),
              ),
              if (e.hint != null)
                Text(
                  e.hint!,
                  style: TextStyle(
                    fontSize: 10.5,
                    color: t.textTertiary,
                    letterSpacing: .4,
                  ),
                ),
              if (e.selected)
                Icon(Icons.check_rounded, size: 12, color: t.accent),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- surfaces

/// Raised pill: lighter than its surroundings with one soft shadow toward the
/// bottom-right and a hairline of light along the top lip.
class RaisedPill extends StatelessWidget {
  const RaisedPill({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    this.radius = AppTokens.radiusPill,
    this.color,
    this.glow,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;
  final Color? color;
  final Color? glow;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: color ?? t.pill,
        borderRadius: BorderRadius.circular(radius),
        // 上浅下深的两条边配圆角会触发 Flutter 的断言（圆角要求四边同色），
        // 所以上沿的高光改用一条贴边的浅色投影。
        border: Border.all(color: Colors.black.withAlpha(58)),
        boxShadow: [
          if (glow != null)
            BoxShadow(
              color: glow!.withAlpha(90),
              blurRadius: 16,
              spreadRadius: -2,
            ),
          BoxShadow(
            color: t.embossLight.withAlpha(34),
            blurRadius: 0,
            offset: const Offset(0, -1),
          ),
          BoxShadow(
            color: Colors.black.withAlpha((t.shadowAlpha * 120).round()),
            blurRadius: 8,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Padding(padding: padding, child: child),
    );
  }
}

/// Recessed well: inner shadow all the way round, darker top-left lip.
class WellBox extends StatelessWidget {
  const WellBox({
    super.key,
    required this.child,
    this.radius = AppTokens.radiusCard,
    this.padding = EdgeInsets.zero,
    this.color,
    this.border = true,
  });

  final Widget child;
  final double radius;
  final EdgeInsetsGeometry padding;
  final Color? color;
  final bool border;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: color ?? t.well,
        borderRadius: BorderRadius.circular(radius),
        border: border
            ? Border.fromBorderSide(
                BorderSide(color: Colors.black.withAlpha(120)),
              )
            : null,
        boxShadow: t.inset,
      ),
      child: Padding(padding: padding, child: child),
    );
  }
}

/// Carved groove: dark hairline over a light hairline.
class Groove extends StatelessWidget {
  const Groove({
    super.key,
    this.axis = Axis.horizontal,
    this.thickness = 1,
    this.inset = 0,
  });

  final Axis axis;
  final double thickness;
  final double inset;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(
        axis == Axis.horizontal ? inset : 0,
        0,
        0,
        0,
      ),
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: axis == Axis.horizontal
              ? LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    Colors.black.withAlpha(110),
                    t.grooveLight,
                    Colors.transparent,
                  ],
                  stops: const [0, .55, 1],
                )
              : LinearGradient(
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                  colors: [
                    Colors.black.withAlpha(110),
                    t.grooveLight,
                    Colors.transparent,
                  ],
                  stops: const [0, .55, 1],
                ),
        ),
        child: SizedBox(
          width: axis == Axis.horizontal ? null : thickness,
          height: axis == Axis.horizontal ? thickness : null,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- controls

class GhostButton extends StatefulWidget {
  const GhostButton({
    super.key,
    required this.label,
    required this.onTap,
    this.icon,
    this.trailing,
    this.leading,
    this.dense = false,
    this.active = false,
    this.width,
    this.onTapAt,
    this.onTapRect,
  });

  final String label;
  final VoidCallback onTap;

  /// Receives the pointer-down position so callers can anchor a popover to it.
  final void Function(Offset global)? onTapAt;

  /// Like [onTapAt], but hands over the button's box so a dropdown can open
  /// fixed against it instead of under the cursor.
  final void Function(Rect box)? onTapRect;
  final IconData? icon;
  final Widget? trailing;
  final Widget? leading;
  final bool dense;
  final bool active;
  final double? width;

  @override
  State<GhostButton> createState() => _GhostButtonState();
}

class _GhostButtonState extends State<GhostButton> {
  bool _hot = false;
  bool _down = false;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hot = true),
      onExit: (_) => setState(() => _hot = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        onTapDown: (d) {
          setState(() => _down = true);
          widget.onTapAt?.call(d.globalPosition);
          widget.onTapRect?.call(globalRect(context));
        },
        onTapUp: (_) => setState(() => _down = false),
        onTapCancel: () => setState(() => _down = false),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 110),
          width: widget.width,
          padding: EdgeInsets.symmetric(
            horizontal: widget.dense ? 9 : 14,
            vertical: widget.dense ? 5.5 : 8,
          ),
          decoration: BoxDecoration(
            color: widget.active
                ? t.surfaceRaised
                : (_hot ? t.surfaceRaised.withAlpha(200) : t.pill),
            borderRadius: BorderRadius.circular(AppTokens.radiusPill),
            border: Border.all(color: t.pillBorder),
            boxShadow: _down
                ? t.inset
                : [
                    BoxShadow(
                      color: Colors.black.withAlpha(
                        (t.shadowAlpha * 90).round(),
                      ),
                      blurRadius: 6,
                      offset: const Offset(0, 2),
                    ),
                  ],
          ),
          child: Row(
            mainAxisSize: widget.width == null
                ? MainAxisSize.min
                : MainAxisSize.max,
            children: [
              if (widget.leading != null) widget.leading!,
              if (widget.icon != null) ...[
                Icon(widget.icon, size: 14, color: t.textSecondary),
                const SizedBox(width: 7),
              ],
              Text(
                widget.label,
                style: TextStyle(
                  fontSize: 13,
                  height: 1.1,
                  color: widget.active ? t.textPrimary : t.textSecondary,
                  fontWeight: widget.active ? FontWeight.w600 : FontWeight.w500,
                ),
              ),
              if (widget.trailing != null) ...[
                const SizedBox(width: 6),
                widget.trailing!,
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class IconSquare extends StatefulWidget {
  const IconSquare({
    super.key,
    required this.icon,
    required this.onTap,
    this.size = 28,
    this.active = false,
    this.tint,
    this.tooltip,
    this.filled = false,
    this.onTapAt,
    this.onTapRect,
    this.iconSize,
  });

  final IconData icon;
  final VoidCallback onTap;
  final void Function(Offset global)? onTapAt;

  /// Like [onTapAt], but hands over the square's box for a fixed dropdown.
  final void Function(Rect box)? onTapRect;
  final double size;
  final bool active;
  final Color? tint;
  final String? tooltip;
  final bool filled;

  /// Overrides the size-relative glyph size for cramped rails.
  final double? iconSize;

  @override
  State<IconSquare> createState() => _IconSquareState();
}

class _IconSquareState extends State<IconSquare> {
  bool _hot = false;
  bool _down = false;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final fg = widget.active ? t.textPrimary : (widget.tint ?? t.textSecondary);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hot = true),
      onExit: (_) => setState(() => _hot = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        onTapDown: (d) {
          setState(() => _down = true);
          widget.onTapAt?.call(d.globalPosition);
          widget.onTapRect?.call(globalRect(context));
        },
        onTapUp: (_) => setState(() => _down = false),
        onTapCancel: () => setState(() => _down = false),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 100),
          width: widget.size,
          height: widget.size,
          decoration: BoxDecoration(
            color: widget.filled
                ? (t.isDark ? Colors.white : t.textPrimary)
                : (_down
                      ? t.well
                      : (_hot || widget.active ? t.surfaceRaised : t.pill)),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: widget.filled ? Colors.transparent : t.pillBorder,
            ),
            boxShadow: _down
                ? t.inset
                : [
                    BoxShadow(
                      color: Colors.black.withAlpha(
                        (t.shadowAlpha * 80).round(),
                      ),
                      blurRadius: 5,
                      offset: const Offset(0, 2),
                    ),
                  ],
          ),
          child: Center(
            child: Icon(
              widget.icon,
              size: widget.iconSize ?? widget.size * .46,
              color: widget.filled
                  ? (t.isDark ? t.wellDeep : Colors.white)
                  : fg,
            ),
          ),
        ),
      ),
    );
  }
}

/// Reusable "value + caret" field that opens a popover list.
class PickerField extends StatelessWidget {
  const PickerField({
    super.key,
    required this.value,
    required this.options,
    required this.onChanged,
    this.width = 92,
    this.title,
    this.colors,
    this.compact = false,
  });

  final String value;
  final List<String> options;
  final ValueChanged<String> onChanged;

  /// The floor. A long option widens the field — and with it the card — rather
  /// than being cut off.
  final double width;
  final String? title;
  final List<Color>? colors;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final w = pickerWidthFor(value, minimum: width);
    final at = options.indexOf(value);
    final selectedColor = colors == null || at < 0
        ? null
        : colors![at % colors!.length];
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (d) {
          final pop = PopoverScope.of(context);
          if (pop == null) return;
          pop.show(
            PopoverSpec(
              title: title,
              anchorRect: globalRect(context),
              width: math.max(150, w),
              entries: [
                for (var i = 0; i < options.length; i++)
                  MenuEntry(
                    options[i],
                    selected: options[i] == value,
                    color: colors == null ? null : colors![i % colors!.length],
                  ),
              ],
              onPick: (i) => onChanged(options[i]),
            ),
          );
        },
        child: Container(
          width: w,
          height: compact ? 26 : fieldHeight,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            color: t.wellDeep,
            borderRadius: BorderRadius.circular(AppTokens.radiusField),
            border: Border.all(color: Colors.black.withAlpha(110)),
            boxShadow: t.inset,
          ),
          child: Row(
            children: [
              Text(
                value,
                maxLines: 1,
                style: fieldValueStyle.copyWith(color: t.textSecondary),
              ),
              if (selectedColor != null) ...[
                // 颜色类选项要在文字后面带一颗同色圆点：光看「紫色」两个字，
                // 对不上机身上那颗 LED 实际亮成什么样。
                const SizedBox(width: 6),
                Container(
                  width: 9,
                  height: 9,
                  decoration: BoxDecoration(
                    color: selectedColor,
                    shape: BoxShape.circle,
                    border: Border.all(color: Colors.white.withAlpha(46)),
                  ),
                ),
              ],
              const Spacer(),
              Icon(Icons.expand_more_rounded, size: 13, color: t.textTertiary),
            ],
          ),
        ),
      ),
    );
  }
}

/// Square caret-only field, used where a value has its own editor.
class CaretBox extends StatelessWidget {
  const CaretBox({
    super.key,
    required this.onTapMenu,
    this.title,
    this.size = fieldHeight,
  });

  /// Receives the box's global rect so the list can open against it.
  final void Function(Rect box) onTapMenu;
  final String? title;
  final double size;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (d) => onTapMenu(globalRect(context)),
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            color: t.wellDeep,
            borderRadius: BorderRadius.circular(AppTokens.radiusField),
            border: Border.all(color: Colors.black.withAlpha(110)),
            boxShadow: t.inset,
          ),
          child: Center(
            child: Icon(
              Icons.expand_more_rounded,
              size: 13,
              color: t.textTertiary,
            ),
          ),
        ),
      ),
    );
  }
}

/// 数值框：左右翻手按 ±1 步进，中间那一格可以直接敲数字。
///
/// 旧界面就是这个形状——只能步进的话，端口、周期这类值改起来要点几十次。
class StepperField extends StatefulWidget {
  const StepperField({
    super.key,
    required this.value,
    required this.onStep,
    this.onCommit,
    this.width = 92,
  });

  final int value;
  final ValueChanged<int> onStep;

  /// 输入的是一个绝对值（已按调用方的范围钳好）；不给就退化成只能步进。
  final ValueChanged<int>? onCommit;
  final double width;

  @override
  State<StepperField> createState() => _StepperFieldState();
}

class _StepperFieldState extends State<StepperField> {
  late final TextEditingController _ctl = TextEditingController(
    text: '${widget.value}',
  );
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!_focus.hasFocus) _commit();
    });
  }

  @override
  void didUpdateWidget(StepperField old) {
    super.didUpdateWidget(old);
    // 引擎回传同一个值时不要覆盖用户正在敲的内容。
    if (old.value != widget.value && !_focus.hasFocus) {
      _ctl.text = '${widget.value}';
    }
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _commit() {
    final parsed = int.tryParse(_ctl.text.trim());
    if (parsed == null || parsed == widget.value) {
      // 焦点回调还在派发，直接改文本会撞上输入系统的编辑状态更新。
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !_focus.hasFocus) _ctl.text = '${widget.value}';
      });
      return;
    }
    widget.onCommit?.call(parsed);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final text = measureText('${widget.value}');
    return SizedBox(
      // 22 + 3 + value box + 3 + 22: the box follows the number.
      width: math.max(widget.width, 50 + text + 17),
      height: fieldHeight,
      child: Row(
        children: [
          _StepBtn(
            icon: Icons.chevron_left_rounded,
            onTap: () => widget.onStep(-1),
          ),
          Expanded(
            child: Container(
              height: fieldHeight,
              margin: const EdgeInsets.symmetric(horizontal: 3),
              padding: const EdgeInsets.symmetric(horizontal: 5),
              decoration: BoxDecoration(
                color: t.wellDeep,
                borderRadius: BorderRadius.circular(AppTokens.radiusField),
                border: Border.all(color: Colors.black.withAlpha(110)),
                boxShadow: t.inset,
              ),
              child: singleLineBox(
                TextField(
                  controller: _ctl,
                  focusNode: _focus,
                  maxLines: 1,
                  textAlign: TextAlign.center,
                  textAlignVertical: TextAlignVertical.center,
                  keyboardType: TextInputType.numberWithOptions(signed: true),
                  inputFormatters: [
                    FilteringTextInputFormatter.allow(RegExp(r'[0-9\-]')),
                  ],
                  onSubmitted: (_) => _commit(),
                  style: fieldValueStyle.copyWith(color: t.textSecondary),
                  strutStyle: fieldStrut(fontSize: 11.5),
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                    border: InputBorder.none,
                    enabledBorder: InputBorder.none,
                    focusedBorder: InputBorder.none,
                  ),
                ),
              ),
            ),
          ),
          _StepBtn(
            icon: Icons.chevron_right_rounded,
            onTap: () => widget.onStep(1),
          ),
        ],
      ),
    );
  }
}

class _StepBtn extends StatefulWidget {
  const _StepBtn({required this.icon, required this.onTap});
  final IconData icon;
  final VoidCallback onTap;

  @override
  State<_StepBtn> createState() => _StepBtnState();
}

class _StepBtnState extends State<_StepBtn> {
  bool _hot = false;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hot = true),
      onExit: (_) => setState(() => _hot = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        child: Container(
          width: 22,
          height: fieldHeight,
          decoration: BoxDecoration(
            color: _hot ? t.surfaceRaised : t.pill,
            borderRadius: BorderRadius.circular(AppTokens.radiusField),
            border: Border.all(color: t.pillBorder),
          ),
          child: Center(
            child: Icon(widget.icon, size: 13, color: t.textSecondary),
          ),
        ),
      ),
    );
  }
}

/// Plain editable line that lives inside a node (no route, no dialog).
class InlineField extends StatefulWidget {
  const InlineField({
    super.key,
    required this.controller,
    required this.hint,
    this.maxLines = 1,
    this.numeric = false,
    this.suffix,
    this.minWidth = 70,
    this.focusNode,
    this.onCommit,
  });

  /// 输入结束（回车或移开焦点）就落盘；有了它就不再需要额外的确认键。
  final ValueChanged<String>? onCommit;

  /// Floor for a single-line field. Longer text widens the box instead of
  /// clipping, so the card grows with it.
  final double minWidth;

  final TextEditingController controller;
  final String hint;
  final int maxLines;
  final bool numeric;
  final Widget? suffix;

  /// 外面想知道这一格有没有在被编辑（引擎回传时别盖掉用户正在敲的字）。
  final FocusNode? focusNode;

  @override
  State<InlineField> createState() => _InlineFieldState();
}

class _InlineFieldState extends State<InlineField> {
  FocusNode get _focusNode => widget.focusNode ?? _owned;
  late final FocusNode _owned = FocusNode();

  @override
  void initState() {
    super.initState();
    _focusNode.addListener(_onFocusChange);
    // The box follows what is typed, so it has to rebuild on every keystroke.
    widget.controller.addListener(_onText);
  }

  void _onFocusChange() {
    setState(() => _focus = _focusNode.hasFocus);
    if (_focus) {
      _atFocus = widget.controller.text;
    } else if (widget.onCommit != null && widget.controller.text != _atFocus) {
      // 输入结束（移开焦点）就落盘，不需要额外的确认键。
      widget.onCommit!(widget.controller.text);
    }
  }

  void _onText() {
    if (mounted) setState(() {});
  }

  bool _focus = false;
  String _atFocus = '';

  @override
  void didUpdateWidget(InlineField old) {
    super.didUpdateWidget(old);
    if (old.controller != widget.controller) {
      old.controller.removeListener(_onText);
      widget.controller.addListener(_onText);
    }
    if (old.focusNode != widget.focusNode) {
      (old.focusNode ?? _owned).removeListener(_onFocusChange);
      _focusNode.addListener(_onFocusChange);
      _focus = _focusNode.hasFocus;
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onText);
    _focusNode.removeListener(_onFocusChange);
    // 外面给的焦点节点归外面释放，这里只管自己建的那个。
    if (widget.focusNode == null) _owned.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final single = widget.maxLines == 1;
    final shown = widget.controller.text.isEmpty
        ? widget.hint
        : widget.controller.text;
    return SizedBox(
      width: single ? valueBoxWidth(shown, minimum: widget.minWidth) : null,
      height: single ? fieldHeight : null,
      child: Container(
        decoration: BoxDecoration(
          color: t.wellDeep,
          borderRadius: BorderRadius.circular(AppTokens.radiusField),
          border: Border.all(
            color: _focus
                ? t.edgeActive.withAlpha(120)
                : Colors.black.withAlpha(110),
          ),
          boxShadow: t.inset,
        ),
        child: Row(
          crossAxisAlignment: widget.maxLines > 1
              ? CrossAxisAlignment.start
              : CrossAxisAlignment.center,
          children: [
            Expanded(
              child: Padding(
                padding: fieldValuePadding(maxLines: widget.maxLines),
                child: singleLineBox(
                  TextField(
                    controller: widget.controller,
                    focusNode: _focusNode,
                    maxLines: widget.maxLines,
                    textAlignVertical: single ? TextAlignVertical.center : null,
                    keyboardType: widget.numeric
                        ? TextInputType.number
                        : TextInputType.text,
                    inputFormatters: widget.numeric
                        ? [FilteringTextInputFormatter.digitsOnly]
                        : null,
                    style: fieldValueStyle.copyWith(color: t.textPrimary),
                    strutStyle: fieldStrut(fontSize: 11.5),
                    cursorColor: t.accent,
                    cursorWidth: 1.4,
                    onSubmitted: widget.onCommit,
                    decoration: InputDecoration(
                      isDense: true,
                      contentPadding: EdgeInsets.zero,
                      border: InputBorder.none,
                      hintText: widget.hint,
                      hintStyle: fieldHintStyle(t.textTertiary),
                    ),
                  ),
                  enabled: single,
                ),
              ),
            ),
            if (widget.suffix != null)
              Padding(
                padding: const EdgeInsets.only(right: 6, top: 4),
                child: widget.suffix!,
              ),
          ],
        ),
      ),
    );
  }
}

/// Popover service plumbing (kept here so chrome files need one import).
class PopoverScope extends InheritedWidget {
  const PopoverScope({super.key, required this.service, required super.child});

  final PopoverService service;

  static PopoverService? of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<PopoverScope>()?.service;

  @override
  bool updateShouldNotify(PopoverScope old) => old.service != service;
}

class TinyLabel extends StatelessWidget {
  const TinyLabel(
    this.text, {
    super.key,
    this.color,
    this.size = 9.5,
    this.spacing = 1.1,
  });

  final String text;
  final Color? color;
  final double size;
  final double spacing;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Text(
      text,
      style: TextStyle(
        fontSize: size,
        letterSpacing: spacing,
        color: color ?? t.textTertiary,
        fontWeight: FontWeight.w600,
        height: 1,
      ),
    );
  }
}

class Dot extends StatelessWidget {
  const Dot({super.key, required this.color, this.size = 5, this.glow = false});

  final Color color;
  final double size;
  final bool glow;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
        boxShadow: glow
            ? [BoxShadow(color: color.withAlpha(120), blurRadius: 6)]
            : null,
      ),
    );
  }
}

/// 卡片里的小文本格：失焦或回车即提交，宽度跟着内容走。
///
/// 与 [InlineField] 的区别是它自己拿控制器并负责回显同步——引擎在同一格上
/// 回传结果时不能把用户正在敲的字盖掉。
class CommitField extends StatefulWidget {
  const CommitField({
    super.key,
    required this.text,
    required this.hint,
    required this.onSubmit,
    this.minWidth = 70,
    this.center = true,
  });

  final String text;
  final String hint;
  final double minWidth;
  final bool center;
  final ValueChanged<String> onSubmit;

  @override
  State<CommitField> createState() => _CommitFieldState();
}

class _CommitFieldState extends State<CommitField> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.text,
  );
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(_onFocus);
  }

  void _onFocus() {
    if (!_focus.hasFocus) _commit();
  }

  void _commit() {
    final text = _ctl.text.trim();
    if (text == widget.text) return;
    widget.onSubmit(text);
  }

  @override
  void didUpdateWidget(CommitField old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text && !_focus.hasFocus) _ctl.text = widget.text;
  }

  @override
  void dispose() {
    _focus.removeListener(_onFocus);
    _focus.dispose();
    _ctl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final shown = _ctl.text.isEmpty ? widget.hint : _ctl.text;
    return SizedBox(
      width: valueBoxWidth(shown, minimum: widget.minWidth),
      height: fieldHeight,
      child: Container(
        decoration: BoxDecoration(
          color: t.wellDeep,
          borderRadius: BorderRadius.circular(AppTokens.radiusField),
          border: Border.all(
            color: _focus.hasFocus
                ? t.edgeActive.withAlpha(120)
                : Colors.black.withAlpha(110),
          ),
          boxShadow: t.inset,
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 7),
          child: singleLineBox(
            TextField(
              controller: _ctl,
              focusNode: _focus,
              maxLines: 1,
              textAlign: widget.center ? TextAlign.center : TextAlign.start,
              textAlignVertical: TextAlignVertical.center,
              style: fieldValueStyle.copyWith(color: t.textPrimary),
              strutStyle: fieldStrut(fontSize: 11.5),
              cursorColor: t.accent,
              cursorWidth: 1.4,
              onSubmitted: (_) => _commit(),
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
      ),
    );
  }
}
