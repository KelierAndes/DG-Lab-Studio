import 'package:flutter/material.dart';

import '../app/field_metrics.dart';
import '../app/studio_prefs.dart';
import '../app/tokens.dart';
import '../pages/app_pages.dart';
import 'primitives.dart';

/// 左边缘的可折叠抽屉。导航在顶栏，这里只放当前页的列表；
/// 收起时留一个浮边把手，不占布局宽度。
class StudioChrome extends StatelessWidget {
  const StudioChrome({
    super.key,
    required this.nav,
    required this.prefs,
    required this.drawerTitle,
    required this.drawerCount,
    required this.drawerBody,
    this.width = panelWidth,
  });

  final AppNav nav;
  final StudioPrefs prefs;
  final String drawerTitle;
  final int drawerCount;
  final Widget drawerBody;
  final double width;

  static const railWidth = 0.0;
  static const panelWidth = 278.0;
  static const gap = 8.0;

  /// 抽屉浮在内容之上，页面与画布都用它来避让。
  static double chromeWidth(StudioPrefs prefs) =>
      12 + (prefs.drawerOpen ? panelWidth + gap : 0);

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: Listenable.merge([nav, prefs]),
      builder: (context, _) => Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AnimatedSize(
            duration: const Duration(milliseconds: 160),
            curve: Curves.easeOutCubic,
            alignment: Alignment.topLeft,
            child: prefs.drawerOpen
                ? SizedBox(
                    width: width,
                    child: DrawerPanel(
                      title: drawerTitle,
                      count: drawerCount,
                      nav: nav,
                      prefs: prefs,
                      child: drawerBody,
                    ),
                  )
                : const SizedBox(height: 0),
          ),
        ],
      ),
    );
  }
}

/// 抽屉收起时贴在左边缘的小把手：顶栏已经没有导航栏位了，这里给一个开回来的口。
class DrawerHandle extends StatelessWidget {
  const DrawerHandle({super.key, required this.prefs});

  final StudioPrefs prefs;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: prefs,
      builder: (context, _) => prefs.drawerOpen
          ? const SizedBox.shrink()
          : IconSquare(
              key: const ValueKey('drawer-expand'),
              icon: Icons.keyboard_arrow_right_rounded,
              size: 26,
              iconSize: 15,
              onTap: prefs.toggleDrawer,
              tooltip: '展开左栏',
            ),
    );
  }
}

/// 抽屉外壳：标题行 + 搜索 + 页面给的内容 + 页脚。
class DrawerPanel extends StatelessWidget {
  const DrawerPanel({
    super.key,
    required this.title,
    required this.count,
    required this.nav,
    required this.prefs,
    required this.child,
  });

  final String title;
  final int count;
  final AppNav nav;
  final StudioPrefs prefs;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: t.surface,
        borderRadius: BorderRadius.circular(AppTokens.radiusPanel - 3),
        border: Border.all(color: t.hairline),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withAlpha((t.shadowAlpha * 130).round()),
            blurRadius: 26,
            offset: const Offset(0, 10),
          ),
          ...t.raised(depth: .5),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppTokens.radiusPanel - 3),
        child: Column(
          children: [
            const SizedBox(height: 10),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 0, 10, 0),
              child: Row(
                children: [
                  Expanded(child: SectionLabel(title)),
                  CountChip(count: count),
                  const SizedBox(width: 5),
                  IconSquare(
                    key: const ValueKey('drawer-collapse'),
                    icon: Icons.keyboard_arrow_left_rounded,
                    size: 22,
                    iconSize: 14,
                    onTap: prefs.toggleDrawer,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 4),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              child: DrawerSearch(nav: nav),
            ),
            const SizedBox(height: 6),
            Expanded(child: child),
            DrawerFooter(prefs: prefs),
          ],
        ),
      ),
    );
  }
}

class DrawerSearch extends StatefulWidget {
  const DrawerSearch({super.key, required this.nav});
  final AppNav nav;

  @override
  State<DrawerSearch> createState() => _DrawerSearchState();
}

class _DrawerSearchState extends State<DrawerSearch> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.nav.query,
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
      height: 30,
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
                  onChanged: widget.nav.search,
                  textAlignVertical: TextAlignVertical.center,
                  style: fieldTextStyle(t.textPrimary),
                  strutStyle: fieldStrut(fontSize: 11.5),
                  cursorColor: t.accent,
                  cursorWidth: 1.2,
                  decoration: InputDecoration(
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                    border: InputBorder.none,
                    hintText: '搜索',
                    hintStyle: fieldHintStyle(t.textTertiary),
                  ),
                ),
              ),
            ),
            if (widget.nav.query.isNotEmpty)
              IconSquare(
                icon: Icons.close_rounded,
                size: 20,
                iconSize: 11,
                onTap: () {
                  _ctl.clear();
                  widget.nav.search('');
                },
              ),
            const SizedBox(width: 4),
          ],
        ),
      ),
    );
  }
}

class DrawerFooter extends StatelessWidget {
  const DrawerFooter({super.key, required this.prefs});

  final StudioPrefs prefs;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Container(
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: t.hairline)),
        color: t.well.withAlpha(t.isDark ? 120 : 90),
      ),
      padding: const EdgeInsets.fromLTRB(10, 7, 10, 7),
      child: Row(
        children: [
          Expanded(
            child: Text(
              'DGStudio',
              style: TextStyle(
                fontSize: 11,
                height: 1,
                color: t.textTertiary,
                letterSpacing: .2,
              ),
            ),
          ),
          TinyLabel('明暗', color: t.textTertiary, size: 9),
          const SizedBox(width: 6),
          AnimatedBuilder(
            animation: prefs,
            builder: (context, _) =>
                SwitchPill(on: prefs.lightTheme, onTap: prefs.toggleTheme),
          ),
        ],
      ),
    );
  }
}

/// 抽屉里的通用一行：图标 + 两行文字 + 尾部插槽。
class DrawerRow extends StatelessWidget {
  const DrawerRow({
    super.key,
    this.icon,
    required this.title,
    this.subtitle = '',
    this.trailing,
    this.onTap,
    this.selected = false,
    this.tint,
    this.titleTint,
    this.leading,
  });

  /// 行首图标；给了 [leading] 就不用了。
  final IconData? icon;
  final String title;
  final String subtitle;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool selected;
  final Color? tint;

  /// 标题自己的颜色（模块列表用它把「已启用」的模块点绿）。
  final Color? titleTint;

  /// 自定义行首（模块用它自己的线条标记替掉通用图标）。
  final Widget? leading;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return HoverRow(
      onTap: onTap ?? () {},
      builder: (context, hot) => Container(
        height: 40,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        decoration: BoxDecoration(
          color: selected
              ? t.surfaceRaised.withAlpha(t.isDark ? 120 : 170)
              : hot
              ? Colors.white.withAlpha(8)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(AppTokens.radiusField),
          border: Border.all(
            color: selected ? t.pillBorder : Colors.transparent,
          ),
        ),
        child: Row(
          children: [
            leading ??
                (icon == null
                    ? const SizedBox.shrink()
                    : Icon(
                        icon,
                        size: 15,
                        color: tint ?? (selected ? t.accent : t.textTertiary),
                      )),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 11.5,
                      height: 1.15,
                      color: titleTint ?? t.textSecondary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (subtitle.isNotEmpty)
                    Text(
                      subtitle,
                      // 一行放不下就折到第二行：卡片面板的说明被截成半句话，
                      // 读的人反而更不知道这张卡是干什么的。
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 9.5,
                        height: 1.15,
                        color: t.textTertiary,
                      ),
                    ),
                ],
              ),
            ),
            ?trailing,
          ],
        ),
      ),
    );
  }
}

/// 抽屉的分组标题。
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Text(
      text,
      style: TextStyle(
        fontSize: 10.5,
        height: 1,
        letterSpacing: .8,
        fontWeight: FontWeight.w600,
        color: t.textTertiary,
      ),
    );
  }
}

class EmptyNote extends StatelessWidget {
  const EmptyNote(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 10),
      child: Text(
        text,
        style: TextStyle(fontSize: 11, height: 1, color: t.textTertiary),
      ),
    );
  }
}

class CountChip extends StatelessWidget {
  const CountChip({super.key, required this.count});
  final int count;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Container(
      width: 19,
      height: 16,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: t.wellDeep,
        borderRadius: BorderRadius.circular(5),
        boxShadow: t.inset,
      ),
      child: Text(
        '$count',
        style: TextStyle(fontSize: 9.5, height: 1, color: t.textTertiary),
      ),
    );
  }
}

/// 内凹槽 + 浮起旋钮，与节点卡片里的字段同一套 emboss 语言。
class SwitchPill extends StatelessWidget {
  const SwitchPill({super.key, required this.on, required this.onTap});

  final bool on;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        child: SizedBox(
          width: 34,
          height: 19,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 130),
            decoration: BoxDecoration(
              color: on ? t.accent.withAlpha(t.isDark ? 90 : 70) : t.wellDeep,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: on
                    ? t.accent.withAlpha(150)
                    : Colors.black.withAlpha(120),
              ),
              boxShadow: t.inset,
            ),
            child: AnimatedAlign(
              duration: const Duration(milliseconds: 130),
              curve: Curves.easeOutCubic,
              alignment: on ? Alignment.centerRight : Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 2.5),
                child: Container(
                  width: 13,
                  height: 13,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: on ? t.accent : t.surfaceRaised,
                    boxShadow: t.raised(depth: .4),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 悬停时抬亮一行的背景，抽屉与页面列表都用它。
class HoverRow extends StatefulWidget {
  const HoverRow({super.key, this.child, required this.onTap, this.builder});

  final Widget? child;
  final Widget Function(BuildContext context, bool hot)? builder;
  final VoidCallback onTap;

  @override
  State<HoverRow> createState() => _HoverRowState();
}

class _HoverRowState extends State<HoverRow> {
  bool _hot = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hot = true),
      onExit: (_) => setState(() => _hot = false),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        child: widget.builder != null
            ? widget.builder!(context, _hot)
            : AnimatedContainer(
                duration: const Duration(milliseconds: 110),
                color: _hot ? Colors.white.withAlpha(8) : Colors.transparent,
                child: widget.child,
              ),
      ),
    );
  }
}
