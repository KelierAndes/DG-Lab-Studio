import 'package:flutter/material.dart' hide Stack;

import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../engine/engine_models.dart';
import '../engine/engine_state.dart';
import 'actions.dart';
import 'app_pages.dart';

/// 日志：引擎与本外壳写出的全部行，按级别与关键字筛。
class LogPage extends StatelessWidget {
  const LogPage({
    super.key,
    required this.nav,
    required this.engine,
    required this.actions,
  });

  final AppNav nav;
  final StudioEngine engine;
  final StudioActions actions;

  List<LogEntry> get _shown {
    final level = AppNav.logLevels[nav.logLevel];
    return [
      for (final l in engine.logs.reversed)
        if ((level == '全部' || l.level == level) && nav.matches(l.text)) l,
    ];
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: Listenable.merge([nav, engine]),
      builder: (context, _) {
        final lines = _shown;
        return PageBody(
          pages: nav,
          header: PageHeader(
            title: '日志',
            subtitle: '${lines.length} 行 · 缓冲上限 ${StudioEngine.logCap} 行',
            pages: nav,
            actions: [
              // 右上角只留急停；筛选与清空跟着日志卡走。
              EstopButton(
                key: const ValueKey('log-estop'),
                onTap: () => actions.invoke('engine.emergency_stop'),
              ),
            ],
          ),
          children: [
            SizedBox(
              height: 452,
              child: BentoCard(
                title: '引擎日志',
                accent: t.portNegative,
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TinyLabel('最新在前', color: t.textTertiary, size: 9),
                    const SizedBox(width: 8),
                    OptionPill(
                      label: '级别：${AppNav.logLevels[nav.logLevel]}',
                      active: nav.logLevel != 0,
                      dense: true,
                      onTap: () => nav.setLogLevel(
                        (nav.logLevel + 1) % AppNav.logLevels.length,
                      ),
                    ),
                    const SizedBox(width: 6),
                    OptionPill(
                      key: const ValueKey('log-clear'),
                      label: '清空',
                      active: false,
                      dense: true,
                      onTap: () {
                        engine.clearLogs();
                        actions.toast('日志已清空');
                      },
                    ),
                  ],
                ),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(10, 6, 10, 6),
                  child: lines.isEmpty
                      ? const Center(child: EmptyNote('这个筛选下没有日志行。'))
                      : ListView.builder(
                          padding: EdgeInsets.zero,
                          itemCount: lines.length,
                          itemBuilder: (context, i) {
                            final l = lines[i];
                            return Padding(
                              padding: const EdgeInsets.only(bottom: 3),
                              child: SelectableText.rich(
                                TextSpan(
                                  style: TextStyle(
                                    fontFamily: 'Consolas',
                                    fontSize: 10.5,
                                    height: 1.5,
                                    letterSpacing: .1,
                                  ),
                                  children: [
                                    TextSpan(
                                      text: '${l.clock}  ',
                                      style: TextStyle(color: t.textTertiary),
                                    ),
                                    TextSpan(
                                      text: '${l.levelLabel}  ',
                                      style: TextStyle(
                                        color: _color(t, l.level),
                                      ),
                                    ),
                                    TextSpan(
                                      text: l.text,
                                      style: TextStyle(color: t.textSecondary),
                                    ),
                                  ],
                                ),
                              ),
                            );
                          },
                        ),
                ),
              ),
            ),
          ],
        );
      },
    );
  }

  static Color _color(AppTokens t, String level) => switch (level) {
    'error' => t.portNegative,
    'warn' => const Color(0xFFF5C542),
    'debug' => t.textTertiary,
    _ => t.accent,
  };
}
