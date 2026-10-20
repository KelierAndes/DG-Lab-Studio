import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/app_pages.dart';
import 'package:dgstudio/pages/detector_card.dart';

import 'harness.dart';

void main() {
  Future<(AppShellState, MockEngineTransport)> bootVision(WidgetTester tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(tester, AppShell(engine: engine),
        // 识别参数卡在模块设置卡下面，窗口太矮就落在视口外。
        size: const Size(1320, 1560));
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.modules);
    shell.nav.selectModule('vision_link');
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    return (shell, transport);
  }

  // 测试字体每个字都占满一行宽，行高被放大，卡片里的行要先滚进视口才点得到。
  // 滚出视口的行根本不会被 ListView 建出来，所以「找不到」也要走滚动这条路。
  Future<void> reveal(WidgetTester tester, Finder target) async {
    final list =
        find.descendant(of: find.byType(DetectorCard), matching: find.byType(Scrollable)).first;
    if (tester.any(target)) {
      final box = tester.getRect(target);
      final card = tester.getRect(find.byType(DetectorCard));
      if (box.top >= card.top && box.bottom <= card.bottom) return;
    }
    await tester.dragUntilVisible(target, list, const Offset(0, -80));
    await tester.pump(const Duration(milliseconds: 120));
  }

  Future<void> commit(WidgetTester tester, Finder box, String text) async {
    final field = find.descendant(of: box, matching: find.byType(TextField));
    await tester.tap(field);
    await tester.pump();
    await tester.enterText(field, text);
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 140));
  }

  // 旧界面里这张表是画面识别模块唯一的配置入口；迁移时漏掉了，模块页就只剩通用设置。
  testWidgets('视觉联动模块给出识别参数表', (tester) async {
    await bootVision(tester);
    expect(find.text('识别参数 · 视觉联动'), findsOneWidget);
    expect(find.byKey(const ValueKey('detector-0')), findsOneWidget);
    expect(find.byKey(const ValueKey('detector-name-0')), findsOneWidget);
    expect(find.text('检测数值条'), findsOneWidget, reason: '行为没有按引擎给的 kinds 显示');
    expect(find.text('0% 位置(x/y)'), findsOneWidget, reason: '数值条该有自己的字段');
    expect(find.text('锚点例图'), findsOneWidget);
    expect(find.text('截锚点'), findsOneWidget);
    expect(find.textContaining('实时 0.42'), findsOneWidget, reason: '实时值要从引擎读出来');

    await reveal(tester, find.text('颜色 #RRGGBB'));
    expect(find.text('颜色 #RRGGBB'), findsOneWidget, reason: '另一行是检测颜色');
  });

  testWidgets('改一个字段即整包写回并 reload', (tester) async {
    final (_, transport) = await bootVision(tester);
    final box = find.byKey(const ValueKey('detector-color-1'));
    await reveal(tester, box);
    await commit(tester, box, '00FF88');

    final call = transport.calls.lastWhere((c) => c.$1 == 'detectors.update');
    expect(call.$2['module_id'], 'vision_link');
    expect(call.$2['index'], 1);
    expect(Map<String, Object?>.from(call.$2['patch'] as Map)['values'], {'color': '00FF88'});
    expect(
        find.descendant(of: find.byKey(const ValueKey('detector-1')), matching: find.text('00FF88')),
        findsOneWidget,
        reason: '写回后卡片没回显新值');
  });

  testWidgets('步进框改阈值，百分比按显示值送', (tester) async {
    final (_, transport) = await bootVision(tester);
    final box = find.byKey(const ValueKey('detector-ratio-1'));
    await reveal(tester, box);
    expect(find.descendant(of: box, matching: find.text('6')), findsOneWidget,
        reason: '占比 0.06 要按 6% 显示');
    await tester.tap(
        find.descendant(of: box, matching: find.byIcon(Icons.chevron_right_rounded)));
    await tester.pump(const Duration(milliseconds: 140));

    final call = transport.calls.lastWhere((c) => c.$1 == 'detectors.update');
    expect(Map<String, Object?>.from(call.$2['patch'] as Map)['values'], {'ratio': 7});
  });

  testWidgets('添加参数排出一行新控件', (tester) async {
    final (_, transport) = await bootVision(tester);
    await tester.tap(find.byKey(const ValueKey('detector-add-vision_link')));
    await tester.pump(const Duration(milliseconds: 160));

    expect(transport.calls.any((c) => c.$1 == 'detectors.add'), isTrue);
    await reveal(tester, find.text('param1'));
    expect(find.byKey(const ValueKey('detector-2')), findsOneWidget, reason: '新行没排出来');
  });

  testWidgets('换行为后按新行为的字段排控件', (tester) async {
    final (_, transport) = await bootVision(tester);
    await tester.tap(find.byKey(const ValueKey('detector-kind-0')));
    await tester.pump(const Duration(milliseconds: 240));
    await tester.tap(find.text('检测图片').last);
    await tester.pump(const Duration(milliseconds: 220));

    final call = transport.calls.lastWhere((c) => c.$1 == 'detectors.update');
    expect(Map<String, Object?>.from(call.$2['patch'] as Map)['kind'], 'image');
    expect(find.text('0% 位置(x/y)'), findsNothing, reason: '换成图片后数值条字段要收掉');
    expect(find.text('截例图'), findsOneWidget, reason: '图片行为要能截例图');
  });

  testWidgets('删除一行即整包写回，剩下的行重新编号', (tester) async {
    final (_, transport) = await bootVision(tester);
    await tester.tap(find.byKey(const ValueKey('detector-remove-0')));
    await tester.pump(const Duration(milliseconds: 200));

    final call = transport.calls.lastWhere((c) => c.$1 == 'detectors.remove');
    expect(call.$2['index'], 0);
    expect(find.byKey(const ValueKey('detector-0')), findsOneWidget);
    expect(find.text('ready'), findsOneWidget, reason: '删掉的应当是数值条那行');
    expect(find.text('boss_hp'), findsNothing);
  });

  testWidgets('非画面识别模块不给识别参数入口', (tester) async {
    final (shell, _) = await bootVision(tester);
    shell.nav.selectModule('osc_bridge');
    await tester.pump(const Duration(milliseconds: 220));
    expect(find.textContaining('识别参数'), findsNothing);
  });
}
