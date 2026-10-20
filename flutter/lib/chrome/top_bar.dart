import 'dart:ui' as ui;

import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/widgets.dart';
import 'package:window_manager/window_manager.dart';

import '../app/tokens.dart';
import '../engine/engine_state.dart';
import '../chrome/page_kit.dart';
import '../graph/graph_model.dart';
import '../pages/app_pages.dart';
import 'primitives.dart';

/// 品牌标记：与旧版标题栏同一块蓝渐变方牌 + 手柄图标。
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 26});

  final double size;

  static const _top = Color(0xFF48C8FF);
  static const _bottom = Color(0xFF0D5FD6);

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [_top, _bottom],
        ),
        borderRadius: BorderRadius.circular(size * .26),
        border: Border.all(color: Colors.white.withAlpha(t.isDark ? 45 : 150)),
        boxShadow: [
          BoxShadow(
            color: _bottom.withAlpha(70),
            blurRadius: 12,
            spreadRadius: -3,
          ),
          ...t.raised(depth: .5),
        ],
      ),
      child: Icon(
        Icons.sports_esports_rounded,
        size: size * .58,
        color: Colors.white,
      ),
    );
  }
}

/// 顶栏：品牌 + 页面标题 + 引擎状态 + 窗口控制。
class TopBar extends StatefulWidget {
  const TopBar({
    super.key,
    required this.nav,
    required this.engine,
    required this.onTheme,
    required this.popover,
  });

  final AppNav nav;
  final StudioEngine engine;
  final VoidCallback onTheme;
  final PopoverService popover;

  @override
  State<TopBar> createState() => _TopBarState();
}

class _TopBarState extends State<TopBar> with WindowListener {
  bool _maximized = false;

  @override
  void initState() {
    super.initState();
    windowManager.addListener(this);
  }

  @override
  void dispose() {
    windowManager.removeListener(this);
    super.dispose();
  }

  @override
  void onWindowMaximize() => setState(() => _maximized = true);

  @override
  void onWindowUnmaximize() => setState(() => _maximized = false);

  Future<void> _toggleMaximise() async {
    if (await windowManager.isMaximized()) {
      await windowManager.unmaximize();
    } else {
      await windowManager.maximize();
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: widget.engine,
      builder: (context, _) {
        final s = widget.engine.snapshot;
        final active = s.active;
        return Padding(
          padding: const EdgeInsets.fromLTRB(18, 12, 16, 0),
          child: Stack(
            alignment: Alignment.center,
            children: [
              // 目的地在左上，状态与窗口控制在右上——与套用的那套外壳同构。
              Row(
                children: [
                  const BrandMark(),
                  const SizedBox(width: 14),
                  for (final p in AppPage.values) ...[
                    if (p != AppPage.values.first) const SizedBox(width: 7),
                    Stack(
                      clipBehavior: Clip.none,
                      children: [
                        OptionPill(
                          key: ValueKey('nav-${p.name}'),
                          label: p.label,
                          dense: true,
                          // 与右侧窗口按钮（28）取 85% 左右的同高，太小点不着也看不清。
                          height: 24,
                          fontSize: 11,
                          active: widget.nav.page == p,
                          onTap: () => widget.nav.go(p),
                        ),
                        // 有新版本时「设置」右上角挂一颗红点：不点进设置也看得见。
                        if (p == AppPage.settings &&
                            widget.engine.updateAvailable)
                          Positioned(
                            right: -1,
                            top: -1,
                            child: Container(
                              key: const ValueKey('nav-update-dot'),
                              width: 7,
                              height: 7,
                              decoration: BoxDecoration(
                                color: t.portNegative,
                                shape: BoxShape.circle,
                                border: Border.all(color: t.surface, width: 1.2),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ],
                ],
              ),
              Align(
                alignment: Alignment.centerRight,
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // 有新版时右上角直接给一颗可点的胶囊，点它去设置页更新。
                    if (widget.engine.updateAvailable) ...[
                      GestureDetector(
                        key: const ValueKey('bar-update'),
                        behavior: HitTestBehavior.opaque,
                        onTap: () => widget.nav.go(AppPage.settings),
                        child: StatusChip(
                          text: '可更新 v${widget.engine.updateInfo?['latest'] ?? ''}',
                          tone: 1,
                        ),
                      ),
                      const SizedBox(width: 7),
                    ],
                    StatusChip(
                      text: widget.engine.online
                          ? '${s.backend.toUpperCase()} · ${s.connected ? '已连接' : '未连接'}'
                          : '引擎未连接',
                      tone: !widget.engine.online ? 0 : (s.connected ? 1 : 2),
                    ),
                    if (active != null) ...[
                      const SizedBox(width: 7),
                      StatusChip(
                        text: active.isOutputDevice
                            ? 'A ${active.strength['A'] ?? 0} · B ${active.strength['B'] ?? 0}'
                            : '气压 ${(active.pressure ?? 0).toStringAsFixed(2)}',
                        tone: 3,
                      ),
                    ],
                    const SizedBox(width: 12),
                    IconSquare(
                      key: const ValueKey('bar-theme'),
                      icon: t.isDark
                          ? Icons.dark_mode_rounded
                          : Icons.light_mode_rounded,
                      size: 30,
                      onTap: widget.onTheme,
                      tooltip: '明暗',
                    ),
                    const SizedBox(width: 6),
                    IconSquare(
                      key: const ValueKey('bar-menu'),
                      icon: Icons.more_horiz_rounded,
                      size: 30,
                      onTap: () {},
                      onTapRect: (box) => widget.popover.show(
                        PopoverSpec(
                          title: '菜单',
                          anchorRect: box,
                          width: 168,
                          entries: const [
                            MenuEntry('刷新引擎'),
                            MenuEntry('全屏'),
                            MenuEntry('退出', danger: true),
                          ],
                          onPick: (i) async {
                            switch (i) {
                              case 0:
                                await widget.engine.refresh();
                              case 1:
                                await windowManager.setFullScreen(true);
                              case 2:
                                await windowManager.close();
                            }
                          },
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    IconSquare(
                      key: const ValueKey('bar-min'),
                      icon: Icons.remove_rounded,
                      size: 28,
                      iconSize: 14,
                      onTap: windowManager.minimize,
                    ),
                    const SizedBox(width: 5),
                    IconSquare(
                      key: const ValueKey('bar-max'),
                      icon: _maximized
                          ? Icons.filter_none_rounded
                          : Icons.crop_square_rounded,
                      size: 28,
                      iconSize: 13,
                      onTap: _toggleMaximise,
                    ),
                    const SizedBox(width: 5),
                    IconSquare(
                      key: const ValueKey('bar-close'),
                      icon: Icons.close_rounded,
                      size: 28,
                      iconSize: 14,
                      onTap: windowManager.close,
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

/// 画布右缘：运行 / 缩放 / 适配 / 网格。只在事件流页出现。
class RightRail extends StatelessWidget {
  const RightRail({
    super.key,
    required this.graph,
    required this.leftInset,
    required this.gridOn,
    required this.onToggleGrid,
    required this.onRun,
    required this.onClearLog,
  });

  final GraphController graph;
  final double leftInset;
  final bool gridOn;
  final VoidCallback onToggleGrid;
  final VoidCallback onRun;
  final VoidCallback onClearLog;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconSquare(
          key: const ValueKey('rail-run'),
          icon: Icons.play_arrow_rounded,
          size: 28,
          tint: t.accent,
          onTap: onRun,
        ),
        IconSquare(
          icon: Icons.delete_sweep_outlined,
          size: 28,
          onTap: graph.log.isEmpty ? () {} : onClearLog,
        ),
        const SizedBox(height: 10),
        IconSquare(
          key: const ValueKey('rail-zoom-in'),
          icon: Icons.add_rounded,
          size: 28,
          onTap: () => graph.zoomStep(1.18),
        ),
        const SizedBox(height: 6),
        IconSquare(
          key: const ValueKey('rail-zoom-out'),
          icon: Icons.remove_rounded,
          size: 28,
          onTap: () => graph.zoomStep(1 / 1.18),
        ),
        const SizedBox(height: 6),
        IconSquare(
          key: const ValueKey('rail-fit'),
          icon: Icons.fullscreen_rounded,
          size: 28,
          onTap: () => graph.fitView(graph.viewport, leftInset: leftInset),
        ),
        const SizedBox(height: 6),
        IconSquare(
          icon: Icons.adjust_rounded,
          size: 28,
          onTap: () => graph.resetView(),
        ),
        const SizedBox(height: 6),
        IconSquare(
          icon: Icons.grid_on_rounded,
          size: 28,
          active: gridOn,
          onTap: onToggleGrid,
        ),
      ],
    );
  }
}

/// 画布下方的运行日志浮层。
class OutputConsole extends StatelessWidget {
  const OutputConsole({super.key, required this.graph, required this.onClose});

  final GraphController graph;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: BackdropFilter(
        filter: ui.ImageFilter.blur(sigmaX: 16, sigmaY: 16),
        child: Container(
          width: 372,
          constraints: const BoxConstraints(maxHeight: 188),
          decoration: BoxDecoration(
            color: Color.alphaBlend(t.glassTint.withAlpha(70), t.surface),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: t.glassRim.withAlpha(70)),
            boxShadow: t.raised(depth: 1.2),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(11, 8, 6, 4),
                child: Row(
                  children: [
                    TinyLabel('运行日志 · ${graph.lastStepCount} 步', size: 8.5),
                    const Spacer(),
                    IconSquare(
                      icon: Icons.close_rounded,
                      size: 22,
                      iconSize: 12,
                      onTap: onClose,
                    ),
                  ],
                ),
              ),
              const Groove(),
              Flexible(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(11, 7, 11, 9),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final line in graph.log)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 3),
                          child: Text(
                            line,
                            style: TextStyle(
                              fontFamily: 'Consolas',
                              fontSize: 11,
                              height: 1.45,
                              color: line.startsWith('!')
                                  ? t.portNegative
                                  : line.startsWith('■')
                                  ? t.accent
                                  : line.startsWith('▶')
                                  ? t.textPrimary
                                  : t.textSecondary,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 右下角的遥测角标：设备、电量、气压、活动槽位。
class TelemetryBlock extends StatelessWidget {
  const TelemetryBlock({super.key, required this.engine, this.right = true});

  final StudioEngine engine;
  final bool right;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: engine,
      builder: (context, _) {
        final s = engine.snapshot;
        final active = s.active;
        final rows = <({IconData icon, String text})>[
          (icon: Icons.link_rounded, text: '后端 ${s.backend.toUpperCase()}'),
          (
            icon: Icons.battery_std_rounded,
            text:
                '电量 ${active?.battery == null ? '--' : '${active!.battery}%'}',
          ),
          (
            icon: Icons.compress_rounded,
            text:
                '气压 ${active?.pressure == null ? '--' : active!.pressure!.toStringAsFixed(2)} kPa',
          ),
          (icon: Icons.category_rounded, text: '槽位 ${s.slots.length}'),
        ];
        return Column(
          crossAxisAlignment: right
              ? CrossAxisAlignment.end
              : CrossAxisAlignment.start,
          children: [
            for (final r in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (right) ...[
                      Text(
                        r.text,
                        style: TextStyle(
                          fontSize: 10.5,
                          height: 1,
                          color: t.textTertiary,
                          letterSpacing: .4,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Icon(
                        r.icon,
                        size: 10,
                        color: t.textTertiary.withAlpha(170),
                      ),
                    ] else ...[
                      Icon(
                        r.icon,
                        size: 10,
                        color: t.textTertiary.withAlpha(170),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        r.text,
                        style: TextStyle(
                          fontSize: 10.5,
                          height: 1,
                          color: t.textTertiary,
                          letterSpacing: .4,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}
