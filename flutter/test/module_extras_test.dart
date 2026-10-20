import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/chrome/page_kit.dart';
import 'package:dgstudio/chrome/primitives.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/modules_page.dart' show listRowHeight, listVisibleRows;
import 'package:dgstudio/pages/app_pages.dart';

import 'harness.dart';

void main() {
  Future<(AppShellState, MockEngineTransport)> bootModule(
      WidgetTester tester, String moduleId) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(tester, AppShell(engine: engine),
        size: const Size(1320, 980));
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.modules);
    shell.nav.selectModule(moduleId);
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    return (shell, transport);
  }

  testWidgets('已安装列表与右侧详情同高，列表行不被拉开', (tester) async {
    await bootModule(tester, 'osc_bridge');
    Rect card(bool Function(String title) hit) => tester.getRect(
        find.byWidgetPredicate((w) => w is BentoCard && hit(w.title)));
    final list = card((t) => t == '已安装');
    final detail = card((t) => t.startsWith('详情'));
    expect(detail.bottom, closeTo(list.bottom, 1.5),
        reason: '两栏外框要齐底：短的那栏补齐，而不是各说各话');

    // 列表行按内容顶格排：相邻行距必须一致，不能为了填高度被拉开。
    final tops = ['osc_bridge', 'sound_link', 'vision_link']
        .map((id) => tester
            .getRect(find.byKey(ValueKey('module-$id')))
            .top)
        .toList();
    expect(tops[1] - tops[0], closeTo(tops[2] - tops[1], 0.6),
        reason: '行距被拉大就是「强制平铺」');
  });

  testWidgets('OSC 参数登记：两张表在同一张卡里，设备表只让改名', (tester) async {
    final (_, transport) = await bootModule(tester, 'osc_bridge');
    // 列表型设置项从「设置 · 模块」卡里分出来单独成卡；同一模块的多张表并排在这张卡里。
    final card = find.ancestor(
      of: find.text('参数登记 · OSC 桥'),
      matching: find.byType(BentoCard),
    );
    expect(card, findsOneWidget);
    expect(find.descendant(of: card, matching: find.text('设备 OSC 参数')),
        findsOneWidget);
    expect(find.descendant(of: card, matching: find.text('自定义 OSC 参数')),
        findsOneWidget);

    // 设备那一栏的行由设备决定：没有加 / 删，读写与类型只显文字。
    expect(find.byKey(const ValueKey('list-add-device_params')), findsNothing);
    expect(
        find.byKey(const ValueKey('list-remove-device_params-0')), findsNothing);
    for (final id in ['dir', 'type']) {
      final cell = find.byKey(ValueKey('list-cell-device_params-$id-0'));
      expect(cell, findsOneWidget);
      expect(find.descendant(of: cell, matching: find.byType(TextField)),
          findsNothing,
          reason: '$id 这一格由设备决定，不该是能敲的输入框');
    }

    // 改名走引擎那条改名（校验、画布改接都在那边），绝不整表写回设置。
    final addr = find.byKey(
        const ValueKey('list-cell-device_params-address-0'));
    await tester.enterText(addr, 'avatar/parameters/MyBattery');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 320));
    final rename = transport.calls.lastWhere((c) => c.$1 == 'flow.var_rename');
    expect(rename.$2['name'], 'avatar/parameters/DGLabBattery');
    expect(rename.$2['new'], 'avatar/parameters/MyBattery');
    expect(
        transport.calls.where((c) =>
            c.$1 == 'modules.settings_set' && c.$2['key'] == 'device_params'),
        isEmpty,
        reason: '设备表由引擎现算，写回设置会留下第二份过期状态');
    expect(transport.calls.where((c) => c.$1 == 'modules.settings_get').length,
        greaterThanOrEqualTo(2),
        reason: '改完要把引擎那份表取回来，失败时地址才会收回原值');
    // 引擎回传的表才是准的：本地那一格不能停在用户敲的半成品上。
    await tester.pump(const Duration(milliseconds: 320));
    // 引擎回传的表才是准的：本地那一格不能停在用户敲的半成品上。
    await tester.pump(const Duration(milliseconds: 320));
    expect(find.text('avatar/parameters/DGLabBattery'), findsOneWidget,
        reason: '改名结果要以引擎那份表为准');
    expect(find.text('avatar/parameters/MyBattery'), findsNothing);
    // 一次提交只该发一次改名：回车与失焦会撞在一起。
    expect(
        transport.calls.where((c) => c.$1 == 'flow.var_rename').length, 1,
        reason: '重复提交会把第二次改名打到「找不到这个变量」');
  });

  testWidgets('参数表最多露出五行，其余在卡内滚动', (tester) async {
    await bootModule(tester, 'osc_bridge');
    final table = find.descendant(
      of: find.byKey(const ValueKey('setting-list-osc_bridge-device_params')),
      matching: find.byType(SingleChildScrollView),
    );
    expect(table, findsOneWidget);
    final h = tester.getSize(table).height;
    expect(h, lessThanOrEqualTo(listRowHeight * listVisibleRows + 1),
        reason: '设备一连上就是几十条，全平铺会把整页拉成几屏高');
    // 第七行确实还在，只是要滚出来才看得见。
    expect(find.byKey(const ValueKey('list-cell-device_params-address-6')),
        findsOneWidget);
  });

  testWidgets('设备参数前缀按家族铺开，改一个不丢其它', (tester) async {
    final (_, transport) = await bootModule(tester, 'osc_bridge');
    expect(find.textContaining('个键（复杂项暂只读）'), findsNothing,
        reason: '折叠成「3 个键」就只看得见个数，改不了值');
    expect(find.textContaining('郊狼 · COYOTE'), findsOneWidget);
    expect(find.textContaining('负鼠 · OVC'), findsOneWidget);
    expect(find.textContaining('灵猫 · BMTR'), findsOneWidget);

    final field = find.ancestor(
        of: find.text('DGLabOvc'), matching: find.byType(TextField));
    await tester.enterText(field, 'DGLabOvc2');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 260));
    final call = transport.calls
        .lastWhere((c) => c.$1 == 'modules.settings_set');
    expect(call.$2['key'], 'device_prefixes');
    final map = Map<String, Object?>.from(call.$2['value'] as Map);
    expect(map['OVC'], 'DGLabOvc2');
    expect(map['BMTR'], 'DGLabBmtr', reason: '改一个家族不能把其它前缀丢掉');
    expect(map['COYOTE'], 'DGLab');
  });

  testWidgets('自定义 OSC 参数表：加一行填地址即写回模块设置', (tester) async {
    final (_, transport) = await bootModule(tester, 'osc_bridge');
    expect(find.text('avatar/parameters/Heard'), findsOneWidget);

    final add = find.byKey(const ValueKey('list-add-custom_params'));
    await tester.ensureVisible(add);
    await tester.pump();
    await tester.tap(add);
    await tester.pump();
    final cell = find.byKey(
        const ValueKey('list-cell-custom_params-address-1'));
    expect(cell, findsOneWidget);
    await tester.enterText(cell, '/avatar/parameters/MyParam');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 260));

    final call = transport.calls
        .lastWhere((c) => c.$1 == 'modules.settings_set');
    expect(call.$2['key'], 'custom_params');
    final rows = (call.$2['value'] as List).cast<Map>();
    // 地址原样写回（带不带开头的「/」都行，引擎登记时统一去掉）。
    expect('${rows.last['address']}', endsWith('avatar/parameters/MyParam'),
        reason: '地址就是变量名，事件流按它建卡收发');

    await tester.tap(find.byKey(const ValueKey('list-remove-custom_params-0')));
    await tester.pump(const Duration(milliseconds: 260));
    final after = transport.calls
        .lastWhere((c) => c.$1 == 'modules.settings_set');
    expect((after.$2['value'] as List).length, 1,
        reason: '删掉一行要整表写回');

    // 自定义表的方向与类型可改：这一类整行都归用户。
    final dirCell = find.byKey(const ValueKey('list-cell-custom_params-dir-0'));
    expect(tester.widget(dirCell), isA<PickerField>(),
        reason: '自定义参数的方向要能改，设备表那两格才是只读文字');
  });

  testWidgets('依赖不齐时给出可点的安装依赖', (tester) async {
    final (_, transport) = await bootModule(tester, 'vision_link');
    expect(find.text('numpy'), findsOneWidget);
    expect(find.text('rapidocr-onnxruntime（可选）'), findsOneWidget);
    expect(find.byKey(const ValueKey('deps-install-vision_link')), findsOneWidget,
        reason: '必选依赖缺失时要能就地装');

    await tester.tap(find.byKey(const ValueKey('deps-install-vision_link')));
    await tester.pump(const Duration(milliseconds: 320));

    expect(transport.calls.any((c) => c.$1 == 'modules.deps_install'), isTrue);
    expect(find.byKey(const ValueKey('deps-install-vision_link')), findsNothing,
        reason: '装完还让点第二次就是没回读状态');
  });

  testWidgets('游戏模组行：扫描、选目录、安装', (tester) async {
    final (_, transport) = await bootModule(tester, 'alice_cradle');
    expect(find.text('游戏模组'), findsOneWidget);
    expect(find.text(r'C:\Games\AliceInCradle'), findsOneWidget,
        reason: '已记住的目录要回显');

    await tester.tap(find.byKey(const ValueKey('mods-scan-alice_cradle')));
    await tester.pump(const Duration(milliseconds: 320));
    expect(transport.calls.any((c) => c.$1 == 'modules.mods_scan'), isTrue);

    expect(find.text(r'D:\SteamLibrary\AliceInCradle'), findsOneWidget,
        reason: '扫到多处要给一处处的选择框');
    await tester.tap(find.text(r'D:\SteamLibrary\AliceInCradle'));
    await tester.pump(const Duration(milliseconds: 320));

    await tester.tap(find.byKey(const ValueKey('mods-install-alice_cradle')));
    await tester.pump(const Duration(milliseconds: 320));
    final call = transport.calls.lastWhere((c) => c.$1 == 'modules.mods_install');
    expect(call.$2['root'], r'D:\SteamLibrary\AliceInCradle');
    expect(find.text(r'D:\SteamLibrary\AliceInCradle'), findsOneWidget,
        reason: '装完要把选中的目录留在格子里');
  });

  testWidgets('模块自带默认配置时列出清单并能一键套用', (tester) async {
    final (shell, transport) = await bootModule(tester, 'alice_cradle');
    // 默认配置是「启用那一刻注入」的：没启用时既没注入也没得套，按钮不该露出来。
    expect(find.text('默认配置'), findsNothing);
    expect(find.byKey(const ValueKey('apply-defaults-alice_cradle')),
        findsNothing);

    await shell.engine
        .runModuleTask('alice_cradle', 'modules.set_enabled', {'enabled': true});
    await tester.pump(const Duration(milliseconds: 320));
    await tester.pump();

    expect(find.text('默认配置'), findsOneWidget);
    expect(find.text('事件流「默认事件流」 · 按键映射「默认按键映射」'), findsOneWidget,
        reason: '要看得见模块带了哪几套配置');

    await tester.tap(find.byKey(const ValueKey('apply-defaults-alice_cradle')));
    await tester.pump(const Duration(milliseconds: 320));

    final call = transport.calls.lastWhere((c) => c.$1 == 'modules.apply_defaults');
    expect(call.$2['module_id'], 'alice_cradle');
    expect(shell.engine.status, contains('默认配置'),
        reason: '套用结果要在提示里说清楚，不能点完没反应');
  });

  testWidgets('模块没启用时不显示设置卡，启用后才出来', (tester) async {
    final (shell, _) = await bootModule(tester, 'alice_cradle');
    expect(find.text('设置 · Alice in Cradle 联动'), findsNothing,
        reason: '没启用时改设置文件不生效，整张卡收掉');
    expect(find.text('参数登记 · Alice in Cradle 联动'), findsNothing);

    await shell.engine
        .runModuleTask('alice_cradle', 'modules.set_enabled', {'enabled': true});
    await tester.pump(const Duration(milliseconds: 320));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 120));

    expect(find.text('设置 · Alice in Cradle 联动'), findsOneWidget,
        reason: '启用后设置项要回来');
  });

  testWidgets('市场行只在版本真的变了时给「更新」', (tester) async {
    final (shell, transport) = await bootModule(tester, 'osc_bridge');
    await shell.engine.fetchMarket();
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    Finder inRow(String id, String text) => find.descendant(
        of: find.byKey(ValueKey('market-$id')), matching: find.text(text));

    // 装的就是市场这一版：不给按钮，也别写「更新」。
    expect(inRow('osc_bridge', '已安装'), findsOneWidget);
    expect(find.byKey(const ValueKey('market-btn-osc_bridge')), findsNothing);

    // 没装过的仍然是「安装」。
    expect(inRow('demo_module', '安装'), findsOneWidget);

    // 市场有新版本：这才出现「更新」，点下去走 modules.update。
    final updateBtn = find.byKey(const ValueKey('market-btn-vision_link'));
    expect(updateBtn, findsOneWidget);
    expect(inRow('vision_link', '更新'), findsOneWidget);
    await tester.ensureVisible(updateBtn);
    await tester.pump();
    await tester.tap(updateBtn);
    await tester.pump(const Duration(milliseconds: 320));
    expect(
      transport.calls.any((c) =>
          c.$1 == 'modules.update' && c.$2['module_id'] == 'vision_link'),
      isTrue,
    );

  });

  testWidgets('没有依赖声明的模块不占地方', (tester) async {
    await bootModule(tester, 'osc_bridge');
    expect(find.text('游戏模组'), findsNothing);
    expect(find.text('默认配置'), findsNothing, reason: '没带默认配置就别长出一行');
    expect(find.byKey(const ValueKey('deps-install-osc_bridge')), findsNothing);
  });

  testWidgets('卸载会删掉模块文件，列表里那条真的消失', (tester) async {
    final (_, transport) = await bootModule(tester, 'sound_link');
    final btn = find.byKey(const ValueKey('module-uninstall-sound_link'));
    await tester.tap(btn);
    await tester.pump(const Duration(milliseconds: 200));
    expect(transport.calls.where((c) => c.$1 == 'modules.delete'), isEmpty,
        reason: '第一下只给一次反悔的机会，不该就动手');
    expect(find.text('确认卸载'), findsOneWidget);

    await tester.tap(btn);
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    final call = transport.calls.lastWhere((c) => c.$1 == 'modules.delete');
    expect(call.$2['module_id'], 'sound_link');
    expect(find.byKey(const ValueKey('module-sound_link')), findsNothing,
        reason: '只 unload 的话文件还在，列表里那条也没走——那就是「卸载没反应」');
  });

  // 详情页要能看见模块要求额外装的 pip 包，并能就地读模块自带的 README。
  testWidgets('详情页写明额外 pip 包，README 按钮弹出预览', (tester) async {
    final (shell, transport) = await bootModule(tester, 'vision_link');
    shell.engine.loadExtras('vision_link');
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    expect(find.textContaining('额外 pip 包'), findsOneWidget);
    expect(find.textContaining('声明在 requirements.txt'), findsOneWidget,
        reason: '要能说出这份清单是从哪儿读的');
    expect(find.textContaining('rapidocr-onnxruntime（可选）'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('module-readme-vision_link')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(
      transport.calls.any((c) => c.$1 == 'modules.readme'),
      isTrue,
      reason: '点 README 才去取，别在切模块时顺带拉一份',
    );
    expect(find.text('视觉联动 · README'), findsOneWidget);
    expect(find.textContaining('opencv-python-headless'), findsOneWidget);
    expect(find.text('关闭'), findsOneWidget);

    await tester.tap(find.text('关闭'));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(find.text('视觉联动 · README'), findsNothing);

    // 没随包带 README 的模块要如实说清楚放哪儿，而不是弹一个空框。
    shell.nav.selectModule('osc_bridge');
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('module-readme-osc_bridge')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(find.textContaining('这个模块没有随包携带 README'), findsOneWidget);
    expect(find.textContaining('modules/osc_bridge/README.md'), findsOneWidget);
  });
}
