import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/app/field_metrics.dart';
import 'package:dgstudio/chrome/charts.dart';
import 'package:dgstudio/chrome/nav_rail.dart';
import 'package:dgstudio/chrome/page_kit.dart';
import 'package:dgstudio/chrome/primitives.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/app_pages.dart';

import 'harness.dart';

void main() {
  Future<AppShellState> boot(WidgetTester tester) async {
    final engine = StudioEngine(MockEngineTransport(heartbeat: false));
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
    );
    await tester.pump(const Duration(milliseconds: 120));
    return shell;
  }

  testWidgets('左栏列出七个目的地，点选即切页', (tester) async {
    final shell = await boot(tester);
    for (final p in AppPage.values) {
      expect(
        find.byKey(ValueKey('nav-${p.name}')),
        findsOneWidget,
        reason: '缺少 ${p.name}',
      );
    }
    expect(shell.nav.page, AppPage.dashboard);
    // 概览这一屏要看得见的是：三条协议各自连上来几台、设备列表与最近日志。
    expect(find.text('Socket V4'), findsOneWidget);
    expect(find.text('连接设备'), findsWidgets);
    expect(find.text('最近日志'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('nav-modules')));
    await tester.pump(const Duration(milliseconds: 120));
    expect(shell.nav.page, AppPage.modules);
    expect(find.text('已安装模块'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('nav-log')));
    await tester.pump(const Duration(milliseconds: 120));
    expect(find.text('引擎日志'), findsOneWidget);
  });

  testWidgets('演示引擎的快照会进入顶栏与抽屉', (tester) async {
    final shell = await boot(tester);
    expect(shell.engine.snapshot.slots.length, 3);
    expect(find.text('V4 · 已连接'), findsOneWidget);
    expect(find.text('设备槽位'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('drawer-slot-slot_2')));
    await tester.pump(const Duration(milliseconds: 120));
    expect(shell.nav.selectedSlot, 'slot_2');
  });

  testWidgets('概览：三条协议各报设备数，日志只留最近十行', (tester) async {
    final shell = await boot(tester);
    for (final label in ['Socket V4', 'Socket V3', '蓝牙直连']) {
      expect(find.text(label), findsOneWidget, reason: '少了「$label」这张协议卡');
    }
    expect(find.text('已启用模块'), findsOneWidget);
    // 每台设备一行：通道标签 + 强度 / 气压读数 + 这一路的波形。
    for (final slot in shell.engine.snapshot.slots) {
      expect(
        find.textContaining(slot.label),
        findsWidgets,
        reason: '${slot.label} 没进设备列表',
      );
    }
    final logRow = find.byWidgetPredicate(
      (w) =>
          w.key is ValueKey<String> &&
          (w.key as ValueKey<String>).value.startsWith('dash-log-'),
    );
    for (var i = 0; i < 14; i++) {
      await shell.engine.invoke(
        'engine.set_strength',
        {'channel': 'A', 'value': i},
      );
    }
    await tester.pump();
    expect(shell.engine.logs.length, greaterThan(10));
    expect(logRow, findsNWidgets(10), reason: '概览的日志没截到十条');
  });

  // 概览只把设备信息写成文字：波形图要常驻重绘与按秒取采样，留给控制页。
  testWidgets('概览的设备一行只用文字报强度 / 电量 / 输出状态', (tester) async {
    final shell = await boot(tester);
    expect(find.byType(WaveChart), findsNothing, reason: '概览不该再画波形图');
    expect(find.byType(PressureChart), findsNothing);
    expect(find.textContaining('波形 '), findsWidgets, reason: '波形要有文字状态');
    for (final slot in shell.engine.snapshot.slots) {
      expect(find.textContaining(slot.label), findsWidgets);
    }
    expect(
      find.byWidgetPredicate(
        (w) =>
            w is Text &&
            w.data != null &&
            RegExp(r'^\d+/\d+$').hasMatch(w.data!),
      ),
      findsWidgets,
      reason: 'A/B 强度读数得是数字，不是柱子',
    );
  });

  // 概览右侧那张卡：把事件流里连好的线路写成「核心[CORE] ← 模块[简写]」。
  testWidgets('概览的数据流探测按引擎给的线路排版', (tester) async {
    await boot(tester);
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pump();
    expect(find.text('数据流探测'), findsOneWidget);
    final lines = find.byWidgetPredicate(
      (w) => w is RichText && w.text.toPlainText().contains('郊狼通道 A 强度'),
    );
    expect(lines, findsOneWidget, reason: '进核心那条线路没画出来');
    final text = (tester.widgetList<RichText>(lines).single.text).toPlainText();
    expect(text, contains('[CORE]'), reason: '核心那一端要带简写');
    expect(text, contains('←'), reason: '方向箭头指的就是数据走的方向');
    expect(text, contains('[OSC]'), reason: '模块那一端用模块自己声明的简写');
    expect(
      find.byWidgetPredicate(
        (w) =>
            w is RichText &&
            w.text.toPlainText().contains('郊狼电量百分比') &&
            w.text.toPlainText().contains('→') &&
            w.text.toPlainText().contains('等 2 个'),
      ),
      findsOneWidget,
      reason: '出核心那条：只报首项，其余算进「等 N 个」',
    );
    expect(find.text('快速动作'), findsNothing, reason: '这张卡已经换掉快速动作');
  });

  testWidgets('明暗切换换掉整组取值', (tester) async {
    final shell = await boot(tester);
    expect(shell.tokens.isDark, isTrue);
    await tester.tap(find.byKey(const ValueKey('bar-theme')));
    await tester.pump(const Duration(milliseconds: 120));
    expect(shell.prefs.lightTheme, isTrue);
    expect(shell.tokens.isDark, isFalse);
  });

  testWidgets('抽屉可以收起，页面内容随之铺满', (tester) async {
    final shell = await boot(tester);
    expect(shell.prefs.drawerOpen, isTrue);
    await tester.tap(find.byKey(const ValueKey('drawer-collapse')));
    await tester.pump(const Duration(milliseconds: 200));
    expect(shell.prefs.drawerOpen, isFalse);
  });

  // StepperField 交回来的是 ±1 增量；曾经被当成新值直接写进引擎。
  testWidgets('强度步进按钮发的是增量而不是绝对值', (tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
    );
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.control);
    await tester.pump(const Duration(milliseconds: 120));

    // 加减键现在是每通道一对 − / +（步长由引擎给），波形才是 ‹ / › 步进。
    await tester.tap(find.byKey(const ValueKey('strength-plus-A')).first);
    await tester.pump(const Duration(milliseconds: 120));

    final step = transport.calls.lastWhere(
      (c) => c.$1 == 'engine.add_strength',
    );
    expect(step.$2['delta'], 1);
    expect(step.$2['channel'], 'A');
    expect(shell.engine.snapshot.slots.first.strength['A'], 1);
  });

  // 设置页的按钮在真机上用像素点击不容易命中，这里按 key 验证它们真的可点、
  // 并且点了确实会打到桥上。
  testWidgets('设置页的保存与导出按钮会打到桥', (tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
    );
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.settings);
    await tester.pump(const Duration(milliseconds: 200));

    await tester.tap(find.byKey(const ValueKey('settings-save')));
    await tester.pump(const Duration(milliseconds: 200));
    expect(
      transport.calls.any((c) => c.$1 == 'config.save_all'),
      isTrue,
      reason: '保存到文件 按钮没把请求发出去',
    );

    await tester.tap(find.byKey(const ValueKey('settings-export')));
    await tester.pump(const Duration(milliseconds: 300));
    expect(
      transport.calls.any((c) => c.$1 == 'config.bundle'),
      isTrue,
      reason: '导出全部 按钮没把请求发出去',
    );
  });

  // 外观三个开关曾经只改内存里的 StudioPrefs，重启就回到默认。
  testWidgets('外观开关写回配置，首帧按配置恢复', (tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
    );
    await tester.pump(const Duration(milliseconds: 200));
    expect(
      shell.prefs.lightTheme,
      isFalse,
      reason: '配置里 appearance.dark=true，应保持深色',
    );

    shell.nav.go(AppPage.settings);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.tap(
      find.descendant(
        of: find.byKey(const ValueKey('settings-grid')),
        matching: find.byType(SwitchPill),
      ),
    );
    await tester.pump(const Duration(milliseconds: 200));

    final call = transport.calls.lastWhere((c) => c.$1 == 'settings.set');
    expect(call.$2['key'], 'appearance.grid');
    expect(call.$2['value'], isFalse);
    expect(shell.prefs.gridOn, isFalse);
  });

  testWidgets('每页右上角只有急停：红底白字，切主题也不变', (tester) async {
    final shell = await boot(tester);
    Color estopFill() {
      final box = tester.widget<AnimatedContainer>(
        find.descendant(
          of: find.byType(EstopButton),
          matching: find.byType(AnimatedContainer),
        ),
      );
      return (box.decoration as BoxDecoration).color!;
    }

    for (final p in AppPage.values) {
      shell.nav.go(p);
      await tester.pump(const Duration(milliseconds: 240));
      if (find.byType(PageHeader).evaluate().isEmpty) continue;
      final estop = find.byType(EstopButton);
      expect(estop, findsOneWidget, reason: '${p.name} 页右上角要有急停');
      expect(estopFill(), const Color(0xFFCE3A3A), reason: '${p.name} 页急停底色');
      // 同一排不该再有别的胶囊：页面动作都回正文里去。
      final band = tester.getRect(estop);
      final pills = find.byType(OptionPill);
      for (var i = 0; i < pills.evaluate().length; i++) {
        final pill = tester.getRect(pills.at(i));
        final sameRow =
            pill.center.dy > band.top - 6 && pill.center.dy < band.bottom + 6;
        expect(sameRow, isFalse, reason: '${p.name} 页右上角只该有急停这一颗');
      }
    }

    shell.prefs.toggleTheme();
    await tester.pump(const Duration(milliseconds: 240));
    expect(estopFill(), const Color(0xFFCE3A3A), reason: '红底不能跟着主题变');
    final label = tester.widget<Text>(
      find.descendant(
        of: find.byType(EstopButton),
        matching: find.byType(Text),
      ),
    );
    expect(label.style!.color, Colors.white, reason: '白字也不跟着主题变');
  });

  testWidgets('设置页文本框：没有确认键、与数字框同高、输入结束自动落盘', (tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
    );
    await tester.pump(const Duration(milliseconds: 200));
    shell.nav.go(AppPage.settings);
    await tester.pump(const Duration(milliseconds: 220));

    final field = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.hintText == 'Socket V4 中继地址',
    );
    expect(field, findsOneWidget);
    expect(
      find.descendant(
        of: find.ancestor(of: field, matching: find.byType(WellBox)).first,
        matching: find.byIcon(Icons.check_rounded),
      ),
      findsNothing,
      reason: '确认键已经去掉，靠输入结束落盘',
    );

    final box = find.ancestor(of: field, matching: find.byType(WellBox)).first;
    expect(
      tester.getRect(box).height,
      fieldHeight,
      reason: '文本框要和数字输入框同高，矮一格就盖不住确认区',
    );

    await tester.tap(field);
    await tester.pump();
    await tester.enterText(field, 'ws://10.0.0.5:9998');
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pump(const Duration(milliseconds: 220));
    final call = transport.calls.lastWhere((c) => c.$1 == 'settings.set');
    expect(call.$2['key'], 'connection.v4_url');
    expect(call.$2['value'], 'ws://10.0.0.5:9998');
  });
}
