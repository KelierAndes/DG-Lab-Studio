import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/app/tokens.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/flow/card_palette.dart';
import 'package:dgstudio/flow/flow_card.dart';
import 'package:dgstudio/flow/flow_models.dart';
import 'package:dgstudio/graph/graph_canvas.dart';
import 'package:dgstudio/graph/graph_model.dart';
import 'package:dgstudio/pages/app_pages.dart';

import 'harness.dart';

void main() {
  /// 进事件流页并把引擎目录拉回来。
  Future<(AppShellState, MockEngineTransport)> bootFlow(WidgetTester tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(tester, AppShell(engine: engine));
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.flow);
    await tester.pump(const Duration(milliseconds: 260));
    // load() 在上一帧的 postFrame 回调里才发出，回传要再一帧才落到面板上。
    await tester.pump();
    return (shell, transport);
  }

  testWidgets('右键空白处是带快捷搜索的建卡面板', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    final canvas = find.byKey(const Key('canvas'));
    await tester.tap(canvas, buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 200));

    final search = find.byKey(const ValueKey('palette-search'));
    expect(search, findsOneWidget, reason: '右键应该直接开搜索面板，而不是两级分类菜单');
    // 面板一开就得抢到键盘焦点：不抢的话右键后直接打字会落到画布上，列表永不过滤。
    final editable = find.descendant(of: search, matching: find.byType(EditableText));
    expect(tester.widget<EditableText>(editable).focusNode.hasFocus, isTrue);

    await tester.enterText(search, '强度');
    await tester.pump(const Duration(milliseconds: 120));
    final palette = find.byType(CardPalette);
    expect(find.descendant(of: palette, matching: find.text('强度写入')), findsOneWidget);
    expect(find.descendant(of: palette, matching: find.text('相加')), findsNothing,
        reason: '搜索要先把不相关的卡滤掉');

    await tester.tap(find.descendant(of: palette, matching: find.text('强度写入')));
    await tester.pump(const Duration(milliseconds: 220));
    final added = transport.calls.lastWhere((c) => c.$1 == 'flow.node_add');
    expect(added.$2['def'], 'core.strength');
    expect(shell.graph.nodes.length, 1);
  });

  testWidgets('建卡面板左上角严格对齐光标（靠边只改大小不改位置）', (tester) async {
    await bootFlow(tester);
    final canvasBottomRight = tester.getBottomRight(find.byKey(const Key('canvas')));
    // 正中 / 贴右 / 贴下：三种落位都要左上角不动，贴边时面板自己变窄变矮。
    for (final at in [
      const Offset(700, 400),
      const Offset(1100, 500),
      const Offset(700, 700),
    ]) {
      await tester.tapAt(at, buttons: kSecondaryButton);
      await tester.pump(const Duration(milliseconds: 200));
      final palette = find.byType(CardPalette);
      expect(palette, findsOneWidget, reason: '光标 $at 处应弹出建卡面板');
      final topLeft = tester.getTopLeft(palette);
      final size = tester.getSize(palette);
      expect(topLeft.dx, at.dx, reason: '面板左上角要压在光标上，不能往左跳');
      expect(topLeft.dy, at.dy, reason: '面板左上角要压在光标上，不能往上跳');
      expect(topLeft.dx + size.width, lessThanOrEqualTo(canvasBottomRight.dx + .5));
      expect(topLeft.dy + size.height, lessThanOrEqualTo(canvasBottomRight.dy + .5));
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pump(const Duration(milliseconds: 120));
      expect(find.byType(CardPalette), findsNothing);
    }
  });

  testWidgets('卡片右键菜单也是左上角对齐光标', (tester) async {
    await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 220));
    final card = find.byType(FlowNodeView);
    expect(card, findsOneWidget);
    final at = tester.getTopLeft(card) + const Offset(60, 12);
    await tester.tapAt(at, buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 200));
    final menu = find.byKey(const ValueKey('popover-menu'));
    expect(menu, findsOneWidget);
    expect(tester.getTopLeft(menu), at, reason: '菜单以前朝上开，指针和卡片差一整个菜单高');
  });

  testWidgets('把线拉到空白处：按类型过滤并自动接线', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    // 先放一张「数值」（只有一个 float 输出）。
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 220));
    final src = shell.graph.nodes.single;

    shell.graph.beginLink(PortRef(src.id, 'out:0'), const Offset(600, 400));
    shell.graph.commitLink(const Offset(600, 400));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.textContaining('仅显示可接上「数值」的卡片'), findsOneWidget,
        reason: '拉线进来的面板要说明在过滤什么');
    // 面板里的行按 def key 定位：抽屉里也列着同名卡片，按文字找会重复。
    expect(find.byKey(const ValueKey('palette-row-core.strength')), findsOneWidget,
        reason: 'float 输入接得上');
    expect(find.byKey(const ValueKey('palette-row-math.add')), findsOneWidget);
    expect(find.byKey(const ValueKey('palette-row-var.const_float')), findsNothing,
        reason: '它没有 float 输入，接不上这根线');

    // 先打字过滤一次：搜索框拿到焦点，下面那次「失焦」才等价于真人点行。
    await tester.enterText(find.byKey(const ValueKey('palette-search')), 'add');
    await tester.pump(const Duration(milliseconds: 120));
    // 面板不能因为搜索框失焦就自己收掉：点行时先失焦，面板在按下那一刻被摘掉，
    // 抬起时的 tap 就永远落空（成品表现：菜单拉起来了，点了没反应）。
    tester.binding.focusManager.primaryFocus?.unfocus();
    await tester.pump(const Duration(milliseconds: 60));
    final row = find.byKey(const ValueKey('palette-row-math.add'));
    expect(row, findsOneWidget, reason: '收起只该由点外面 / Esc 触发');

    await tester.tap(row);
    await tester.pump(const Duration(milliseconds: 260));

    expect(transport.calls.any((c) => c.$1 == 'flow.node_add'), isTrue);
    final wire = transport.calls.lastWhere((c) => c.$1 == 'flow.wire_add');
    expect(wire.$2['src'], [src.id, 0], reason: '起点得是刚拉出来的那个输出');
    expect((wire.$2['dst'] as List)[0], isNot(src.id));
    expect((wire.$2['dst'] as List)[1], 0, reason: '要接到新卡第一个空着的兼容输入');
  });

  testWidgets('节点卡按类型带主题色：顶边与标题同色，不同类型分得开', (tester) async {
    await bootFlow(tester);
    for (final key in ['math.add', 'core.strength', 'var.const_float']) {
      await tester.tap(find.byKey(ValueKey('palette-$key')));
      await tester.pump(const Duration(milliseconds: 180));
    }

    Color? rail(String title) {
      for (final e in find
          .ancestor(of: find.text(title), matching: find.byType(DecoratedBox))
          .evaluate()) {
        final d = (e.widget as DecoratedBox).decoration;
        if (d is BoxDecoration && (d.border?.top.width ?? 0) > 1) {
          return d.border!.top.color;
        }
      }
      return null;
    }

    expect(rail('相加'), flowCategoryColors['数学'],
        reason: '数学类卡片要带自己那档颜色');
    expect(rail('强度写入'), flowCategoryColors['核心写入']);
    expect(rail('数值'), flowCategoryColors['常数']);
    final titles = ['相加', '强度写入', '数值']
        .map((s) => tester.widgetList<Text>(find.text(s)).first.style!.color)
        .toList();
    expect(titles.toSet().length, 3, reason: '标题色三张三个样，才叫按类型区分');
  });

  testWidgets('整理按语义分五列，列内保持原有上下顺序', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    for (final key in ['driver.period', 'var.const_float', 'math.add', 'core.strength']) {
      await tester.tap(find.byKey(ValueKey('palette-$key')));
      await tester.pump(const Duration(milliseconds: 180));
    }
    // 手动把「相加」拖到最上面，整理后列内顺序要按 y 保留。
    shell.graph.nodes
        .firstWhere((n) => '${n.data['defKey']}' == 'math.add')
        .move(const Offset(-40, -40));
    await tester.pump(const Duration(milliseconds: 120));

    await tester.tap(find.byKey(const ValueKey('flow-arrange')));
    await tester.pump(const Duration(milliseconds: 300));

    final move = transport.calls.lastWhere((c) => c.$1 == 'flow.nodes_move');
    final moves = Map<String, Object?>.from(move.$2['moves'] as Map);
    expect(moves.length, 4);
    Offset posOf(String defKey) {
      final node = shell.graph.nodes.firstWhere((n) => '${n.data['defKey']}' == defKey);
      final m = moves[node.id] as List;
      return Offset((m[0] as num).toDouble(), (m[1] as num).toDouble());
    }

    // 输入页整页镜像（与旧版一致）：数据右 → 左流，所以从左到右是 汇 → 运算 → 源 → 事件。
    expect(posOf('core.strength').dx, lessThan(posOf('math.add').dx));
    expect(posOf('math.add').dx, lessThan(posOf('var.const_float').dx));
    expect(posOf('var.const_float').dx, lessThan(posOf('driver.period').dx));
    // 每列都从 y=40 起排。
    expect(posOf('driver.period').dy, 40);
    // 提示要按这一页实际放的卡片来讲：输入页的源是模块变量、汇是核心写入。
    expect(shell.engine.status, contains('模块变量传出'));
    expect(shell.engine.status, contains('核心数据接收'));
  });

  // 镜像不只影响列序，引脚也得跟着换边——否则标签与线各说各话。
  testWidgets('输入页引脚换边，输出页保持入左出右', (tester) async {
    final (shell, _) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-math.add')));
    await tester.pump(const Duration(milliseconds: 220));
    final mirrored = shell.graph.nodes.single;
    final input = mirrored.ports.firstWhere((p) => p.dir == PortDir.input);
    final output = mirrored.ports.firstWhere((p) => p.dir == PortDir.output);
    expect(mirrored.portWorld(output).dx, lessThan(mirrored.portWorld(input).dx),
        reason: '输入页：出脚在左、入脚在右');
    expect(mirrored.portWorld(output).dx, closeTo(mirrored.position.dx + output.dx, .01));

    await tester.tap(find.descendant(
        of: find.byKey(const ValueKey('flow-pages')), matching: find.text('输出')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.tap(find.byKey(const ValueKey('palette-math.add')));
    await tester.pump(const Duration(milliseconds: 220));
    final plain = shell.graph.nodes.firstWhere((n) => '${n.data['defKey']}' == 'math.add');
    final pIn = plain.ports.firstWhere((p) => p.dir == PortDir.input);
    final pOut = plain.ports.firstWhere((p) => p.dir == PortDir.output);
    expect(plain.portWorld(pOut).dx, greaterThan(plain.portWorld(pIn).dx),
        reason: '输出页不镜像：入左出右');
  });

  // 输出页不镜像：那页卡片本来就是入左出右，列序照数据流从左往右铺。
  testWidgets('输出页整理从左往右，不按列镜像', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await tester.tap(find.descendant(
        of: find.byKey(const ValueKey('flow-pages')), matching: find.text('输出')));
    await tester.pump(const Duration(milliseconds: 260));
    for (final key in ['core.strength', 'var.const_float', 'math.add']) {
      await tester.tap(find.byKey(ValueKey('palette-$key')));
      await tester.pump(const Duration(milliseconds: 180));
    }

    await tester.tap(find.byKey(const ValueKey('flow-arrange')));
    await tester.pump(const Duration(milliseconds: 300));

    final move = transport.calls.lastWhere((c) => c.$1 == 'flow.nodes_move');
    final moves = Map<String, Object?>.from(move.$2['moves'] as Map);
    Offset posOf(String defKey) {
      final node = shell.graph.nodes.firstWhere((n) => '${n.data['defKey']}' == defKey);
      final m = moves[node.id] as List;
      return Offset((m[0] as num).toDouble(), (m[1] as num).toDouble());
    }

    expect(posOf('var.const_float').dx, lessThan(posOf('math.add').dx));
    expect(posOf('math.add').dx, lessThan(posOf('core.strength').dx),
        reason: '汇（核心数值传出到模块变量）要在最右，不能翻到最左');
    expect(shell.engine.status, contains('核心数值输出'));
  });

  testWidgets('空画布点整理只会提醒，不会发一批空坐标', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    expect(shell.graph.nodes, isEmpty, reason: '默认这张画布是空的');
    await tester.tap(find.byKey(const ValueKey('flow-arrange')));
    await tester.pump(const Duration(milliseconds: 200));
    expect(transport.calls.any((c) => c.$1 == 'flow.nodes_move'), isFalse);
    expect(shell.engine.status, contains('这张画布还是空的'));
  });

  // 用户反馈：弹出面板与光标差得远。用测试量出真实偏差，别靠眼睛估。
  testWidgets('建卡面板左上角就落在光标处', (tester) async {
    await bootFlow(tester);
    final at = const Offset(620, 430);
    await tester.tapAt(at, buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 200));
    expect(tester.getTopLeft(find.byType(CardPalette)), at,
        reason: '面板左上角应与光标重合，不能偏出去一截');
  });

  Future<void> pickVarTab(WidgetTester tester) async {
    await tester.tap(find.descendant(
        of: find.byKey(const ValueKey('flow-drawer-tabs')), matching: find.text('变量表')));
    await tester.pump(const Duration(milliseconds: 120));
    await tester.pump();
  }

  testWidgets('进事件流页会向引擎要目录，点面板项就建卡', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    expect(transport.calls.any((c) => c.$1 == 'flow.state'), isTrue, reason: '没向引擎取过图');
    expect(find.byKey(const ValueKey('palette-var.const_float')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 200));

    final added = transport.calls.lastWhere((c) => c.$1 == 'flow.node_add');
    expect(added.$2['def'], 'var.const_float');
    expect(shell.graph.nodes.length, 1);
    expect(find.byType(FlowNodeView), findsOneWidget);
  });

  // 数值格只能点加减键、敲不进数字，是这一轮报的「所有输入框都无法输入」。
  testWidgets('卡片数值格可以直接敲数字', (tester) async {
    final (_, transport) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 200));

    final box = find.descendant(of: find.byType(FlowNodeView), matching: find.byType(TextField));
    expect(box, findsOneWidget);
    await tester.enterText(box, '7');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump();

    final set = transport.calls.lastWhere((c) => c.$1 == 'flow.node_set');
    expect(set.$2['params'], {'v': 7.0});
    expect(find.text('7.00'), findsOneWidget);
  });

  // 改一次值就重新取景 = 正在编辑的卡片被挪走，看起来像视图被重置。
  testWidgets('改卡片值不会重新取景', (tester) async {
    final (shell, _) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 200));

    final before = (shell.graph.pan.dx, shell.graph.pan.dy, shell.graph.scale);
    await tester.tap(find.byIcon(Icons.chevron_right_rounded).last);
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(
      (shell.graph.pan.dx, shell.graph.pan.dy, shell.graph.scale),
      before,
      reason: '改一个参数值不该把视图重新取景',
    );
  });

  // 参数写回后画布要显示引擎那一份，不然改一次值就弹回默认。
  testWidgets('卡片步进改值后画布显示引擎回传的值', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 200));
    expect(shell.graph.nodes.length, 1);

    await tester.tap(find.byIcon(Icons.chevron_right_rounded).last);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump();

    final set = transport.calls.lastWhere((c) => c.$1 == 'flow.node_set');
    expect(set.$2['params'], {'v': 0.1});
    expect(find.text('0.10'), findsOneWidget);
  });

  testWidgets('卡片右键：复制与删除都打到引擎', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 200));

    final header = tester.getTopLeft(find.byType(FlowNodeView)) + const Offset(60, 12);
    await tester.tapAt(header, buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 120));
    expect(find.text('复制并另起一张'), findsOneWidget);

    await tester.tap(find.text('复制并另起一张'));
    await tester.pump(const Duration(milliseconds: 220));
    expect(shell.graph.nodes.length, 2, reason: '副本没进画布：复制应当由引擎登记');
    expect(transport.calls.where((c) => c.$1 == 'flow.node_add').length, 2);

    await tester.tapAt(tester.getTopLeft(find.byType(FlowNodeView).last) + const Offset(60, 12),
        buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 120));
    await tester.tap(find.text('删除'));
    await tester.pump(const Duration(milliseconds: 320));
    expect(transport.calls.any((c) => c.$1 == 'flow.node_remove'), isTrue);
    expect(shell.graph.nodes.length, 1);
  });

  testWidgets('变量表：系统变量只读，改名回到模块配置页那一栏', (tester) async {
    final (_, transport) = await bootFlow(tester);
    await pickVarTab(tester);

    expect(find.textContaining('系统变量 · 模块登记（2）'), findsOneWidget);
    expect(find.textContaining('临时变量 · 自己登记（1）'), findsOneWidget);
    expect(find.text('这一栏只读：名字与读写由模块自己的配置页决定。'), findsOneWidget);
    expect(find.byKey(const ValueKey('var-spawn-Coyote.StrengthA')), findsOneWidget);
    expect(find.byKey(const ValueKey('var-spawn-combo')), findsOneWidget);
    // 模块登记的行不给改名框：在这儿改了也不算数，名字跟着模块那一栏走。
    expect(find.text('avatar/parameters/DGLabBattery'), findsOneWidget);
    expect(
        find.byKey(
            const ValueKey('var-rename-avatar/parameters/DGLabBattery')),
        findsNothing);
    expect(find.byKey(const ValueKey('var-rename-Coyote.StrengthA')), findsNothing);
    // 类型与读写由模块定：系统组的行也没有删除键，方向胶囊点不动。
    expect(
        find.byKey(
            const ValueKey('var-del-avatar/parameters/DGLabBattery')),
        findsNothing);
    expect(find.text('写'), findsNWidgets(2)); // 强度 A 与设备电量都只出不进
    expect(find.text('读写'), findsOneWidget); // combo 双向

    // 临时变量照旧能改名。
    await tester.enterText(find.byKey(const ValueKey('var-rename-combo')), 'rush');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 200));
    final rename = transport.calls.lastWhere((c) => c.$1 == 'flow.var_rename');
    expect(rename.$2['name'], 'combo');
    expect(rename.$2['new'], 'rush');
  });

  // 数据线没有方向性，只有执行引脚画箭头；箭头方向 = 这一页数据走的方向。
  testWidgets('只有执行引脚带箭头，指向这一页的流向', (tester) async {
    final (shell, _) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-math.add')));
    await tester.pump(const Duration(milliseconds: 220));
    await tester.tap(find.byKey(const ValueKey('palette-core.strength')));
    await tester.pump(const Duration(milliseconds: 220));
    await tester.tap(find.byKey(const ValueKey('palette-driver.period')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    final byId = {for (final n in shell.graph.nodes) n.id: n};
    final data = byId.values.firstWhere(
      (n) => '${n.data['defKey']}' == 'math.add',
    );
    final write = byId.values.firstWhere(
      (n) => '${n.data['defKey']}' == 'core.strength',
    );
    final driver = byId.values.firstWhere(
      (n) => '${n.data['defKey']}' == 'driver.period',
    );
    expect(driver.mirror, isTrue, reason: '输入页整页镜像：数据右 → 左流');

    for (final p in data.ports) {
      expect(
        find.byKey(ValueKey('pin-arrow-${p.id}-${data.id}')),
        findsNothing,
        reason: '${p.label} 是数据引脚，不该画箭头',
      );
    }
    // 写入卡的执行入口、周期卡的执行出口：都该有箭头，且都朝着流向。
    for (final node in [write, driver]) {
      final execs = node.ports.where((x) => x.isExec).toList();
      expect(execs, isNotEmpty, reason: '${node.id} 没有执行引脚，测试选错卡了');
      for (final p in execs) {
        final arrow = find.byKey(ValueKey('pin-arrow-${p.id}-${node.id}'));
        expect(arrow, findsOneWidget, reason: '${p.label} 没有方向箭头');
        expect(tester.widget<PinArrow>(arrow).pointsRight, isFalse,
            reason: '输入页的执行箭头该朝左');
      }
    }
    // 卡片边上那颗执行插座三角（「外箭头」）：以前按进 / 出分向画，
    // 镜像页上出脚那颗会反过来指着卡片内部。现在整页统一朝着流向。
    final sockets = tester
        .widgetList<CustomPaint>(
          find.byWidgetPredicate(
            (w) => w is CustomPaint && w.painter is ExecPinPainter,
          ),
        )
        .map((w) => w.painter as ExecPinPainter)
        .toList();
    expect(sockets, isNotEmpty, reason: '画面上没有执行插座');
    expect(
      sockets.every((p) => !p.pointsRight),
      isTrue,
      reason: '输入页的执行插座三角该全部朝左，跟着流向走',
    );
  });

  // 卡片面板只写一个名字，读的人并不知道这张卡干什么。
  testWidgets('卡片面板每一项都带一句说明', (tester) async {
    await bootFlow(tester);
    expect(find.text('两个数相加'), findsOneWidget);
    expect(find.text('一个常数'), findsOneWidget);
    expect(find.text('按周期触发一次'), findsOneWidget);
    expect(find.text('把值写到当前设备的某个通道'), findsOneWidget);
  });

  test('执行箭头朝向：跟着这一页的流向，输出页 ▶、输入页 ◀', () {
    expect(execArrowPointsRight(mirror: false), isTrue);
    expect(execArrowPointsRight(mirror: true), isFalse);
  });

  testWidgets('变量表：新增、改名、换向、删除都按引擎语义发请求', (tester) async {
    final (_, transport) = await bootFlow(tester);
    await pickVarTab(tester);

    await tester.tap(find.byKey(const ValueKey('var-dir-combo')));
    await tester.pump(const Duration(milliseconds: 120));
    expect(transport.calls.lastWhere((c) => c.$1 == 'flow.var_dir').$2['dir'], 'in');

    await tester.enterText(find.byKey(const ValueKey('var-rename-combo')), 'rush');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 160));
    final rename = transport.calls.lastWhere((c) => c.$1 == 'flow.var_rename');
    expect(rename.$2['name'], 'combo');
    expect(rename.$2['new'], 'rush');

    await tester.tap(find.byKey(const ValueKey('var-add')));
    await tester.pump(const Duration(milliseconds: 120));
    await tester.enterText(find.byKey(const ValueKey('var-new-name')), 'heat');
    await tester.tap(find.byKey(const ValueKey('var-add-ok')));
    await tester.pump(const Duration(milliseconds: 160));
    expect(transport.calls.lastWhere((c) => c.$1 == 'flow.var_add').$2['name'], 'heat');
    expect(find.byKey(const ValueKey('var-spawn-heat')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('var-del-heat')));
    await tester.pump(const Duration(milliseconds: 160));
    expect(transport.calls.lastWhere((c) => c.$1 == 'flow.var_remove').$2['name'], 'heat');
    expect(find.byKey(const ValueKey('var-spawn-heat')), findsNothing);
  });

  testWidgets('点变量行的手柄按当前页建读数卡', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await pickVarTab(tester);

    await tester.tap(find.byKey(const ValueKey('var-spawn-combo')));
    await tester.pump(const Duration(milliseconds: 260));

    expect(transport.calls.lastWhere((c) => c.$1 == 'flow.node_add').$2['def'], 'var.read.combo');
    expect(shell.graph.nodes.length, 1);
  });

  // 变量行的手柄可以拖到画布上，在落点建该变量的读数 / 写入卡。
  testWidgets('拖动变量手柄到画布建卡', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await pickVarTab(tester);

    await tester.drag(find.byKey(const ValueKey('var-spawn-combo')),
        const Offset(520, 220));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    final added = transport.calls.lastWhere((c) => c.$1 == 'flow.node_add');
    expect(added.$2['def'], 'var.read.combo');
    expect(shell.graph.nodes.length, 1);
  });

  // 卡片头部要跟着这一拍的实时值走（引擎的 live，格式化也在引擎侧）。
  testWidgets('读数卡头部显示引擎回传的实时值', (tester) async {
    final (shell, _) = await bootFlow(tester);
    await pickVarTab(tester);
    await tester.tap(find.byKey(const ValueKey('var-spawn-combo')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    await tester.pump(const Duration(milliseconds: 700));
    await tester.pump();

    final live = shell.graph.nodes.first.data['live'];
    expect(live, isA<List<Object?>>(), reason: 'flow.live 没落到画布节点上');
    expect((live as List).first, '1');
    expect(
        find.descendant(of: find.byType(FlowNodeView), matching: find.text('1')),
        findsOneWidget);
  });

  // 设备连上以后模块才登记出参数，引擎那边目录会多一批卡片定义。界面只按实时值
  // 那一轮收敛红字和灰显，目录本身不跟着换，读数卡就一直是「只有 key 的空壳」，
  // 而左边的变量表已经出现同名变量——看着像状态没刷新。
  testWidgets('目录换代时整份重取一次，之后不反复拉', (tester) async {
    final (_, transport) = await bootFlow(tester);
    int loads() => transport.calls.where((c) => c.$1 == 'flow.state').length;
    final before = loads();
    expect(before, greaterThan(0), reason: '进页时要先取一次目录');

    transport.catalogRev = 2; // 设备连上：目录多了一批定义
    await tester.pump(const Duration(milliseconds: 700));
    await tester.pump();
    final reloaded = loads();
    expect(reloaded, greaterThan(before), reason: '目录版本变了必须重取整份状态');

    await tester.pump(const Duration(milliseconds: 1400));
    await tester.pump();
    expect(loads(), reloaded, reason: '版本没再变就别一直拉整份状态');
  });

  // 变量名一栏要能联想：打字后列出变量表里的候选，点一下就填进去。
  testWidgets('卡片里的变量名一栏可以联想', (tester) async {
    final (_, transport) = await bootFlow(tester);
    await pickVarTab(tester);
    await tester.tap(find.byKey(const ValueKey('var-spawn-combo')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    final box = find
        .descendant(of: find.byType(FlowNodeView), matching: find.byType(TextField))
        .last;
    await tester.enterText(box, 'com');
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.text('变量'), findsWidgets, reason: '没有弹出联想列表');

    await tester.tap(find.text('combo').last);
    await tester.pump(const Duration(milliseconds: 260));
    final set = transport.calls.lastWhere((c) => c.$1 == 'flow.node_set');
    expect(set.$2['params'], {'name': 'combo'});
  });

  // 刚登记的变量卡不在本地那份目录里：建卡后要补取一次目录，否则卡片只剩一个 key。
  testWidgets('新建变量的卡片会补取目录后正常排版', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await pickVarTab(tester);

    await tester.tap(find.byKey(const ValueKey('var-add')));
    await tester.pump(const Duration(milliseconds: 120));
    await tester.enterText(find.byKey(const ValueKey('var-new-name')), 'heat');
    await tester.tap(find.byKey(const ValueKey('var-add-ok')));
    await tester.pump(const Duration(milliseconds: 160));
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('var-spawn-heat')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    expect(transport.calls.where((c) => c.$1 == 'flow.state').length, greaterThan(1),
        reason: '建卡后没有回头补取目录');
    expect(find.text('读数 · heat'), findsOneWidget);
    expect(shell.graph.nodes.length, 1);
  });

  test('异类型数据脚在事件流画布上允许落线，执行脚仍然拒', () {
    const from = PortSpec(
      id: 'o',
      kind: PortKind.string,
      dir: PortDir.output,
      label: '文本',
      dy: 10,
    );
    const into = PortSpec(
      id: 'i',
      kind: PortKind.integer,
      dir: PortDir.input,
      label: '整数',
      dy: 10,
    );
    const exec = PortSpec(
      id: 'e',
      kind: PortKind.exec,
      dir: PortDir.input,
      label: '执行',
      dy: 10,
    );
    final strict = GraphController(nodes: [], edges: []);
    final flow = GraphController(nodes: [], edges: [], autoConvert: true);
    expect(strict.dropOk(from, into), isFalse, reason: '图像工作流那侧仍然直接拒');
    expect(flow.dropOk(from, into), isTrue, reason: '异类型交给引擎补转换卡');
    expect(flow.dropOk(from, exec), isFalse, reason: '执行流不是差一张转换卡');
  });

  testWidgets('左键框选、中键平移、右键框选即删', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    // 清空本页要双击确认：先拿到一张只有单个卡片的画布，框选的起止点才好算。
    await tester.tap(find.byKey(const ValueKey('flow-clear')));
    await tester.pump(const Duration(milliseconds: 120));
    await tester.tap(find.byKey(const ValueKey('flow-clear')));
    // 双击确认那颗 4 秒的反悔定时器要放掉，否则测试结束时算漏一个 pending timer。
    await tester.pump(const Duration(seconds: 4, milliseconds: 200));
    await tester.pump();
    expect(shell.graph.nodes, isEmpty);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(shell.graph.nodes.length, 1);

    final card = tester.getRect(find.byType(FlowNodeView));
    final from = Offset(card.center.dx - 40, card.bottom + 12);
    final to = Offset(card.center.dx + 120, card.top - 12);

    final pick = await tester.startGesture(from,
        buttons: kPrimaryButton, kind: PointerDeviceKind.mouse);
    await tester.pump();
    await pick.moveTo(to);
    await tester.pump();
    await pick.up();
    await tester.pump(const Duration(milliseconds: 200));
    expect(shell.graph.selection, isNotEmpty, reason: '左键拖框没把卡片选上');

    final pan = shell.graph.pan;
    final slide = await tester.startGesture(from,
        buttons: kTertiaryButton, kind: PointerDeviceKind.mouse);
    await tester.pump();
    await slide.moveTo(from + const Offset(70, 30));
    await tester.pump();
    await slide.up();
    await tester.pump(const Duration(milliseconds: 200));
    expect(shell.graph.pan, isNot(pan), reason: '中键没移动画布');
    expect(shell.graph.nodes.length, 1, reason: '中键拖不该顺手删东西');

    // 中键拖过之后卡片在视口里挪了位置，右框选的起止点要按新位置重算。
    final moved = tester.getRect(find.byType(FlowNodeView));
    final cut = await tester.startGesture(
      Offset(moved.center.dx - 40, moved.bottom + 12),
      buttons: kSecondaryButton,
      kind: PointerDeviceKind.mouse,
    );
    await tester.pump();
    await cut.moveTo(Offset(moved.center.dx + 120, moved.top - 12));
    await tester.pump();
    await cut.up();
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    expect(shell.graph.nodes, isEmpty, reason: '右键框选没删掉圈中的卡片');
    expect(
      transport.calls.any((c) => c.$1 == 'flow.node_remove'),
      isTrue,
      reason: '删除没交给引擎',
    );
    expect(
      find.byType(CardPalette),
      findsNothing,
      reason: '右键拖过的是框选，不该同时弹出建卡菜单',
    );
  });

  testWidgets('选中卡片后 Ctrl+C / Ctrl+V 走引擎，Ctrl+S 保存', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    final before = shell.graph.nodes.length;
    shell.graph.selection.addAll(shell.graph.nodes.map((n) => n.id));

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyC);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    expect(shell.engine.status, contains('已复制'));

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyV);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    expect(
      transport.calls.any((c) => c.$1 == 'flow.paste'),
      isTrue,
      reason: 'Ctrl+V 没交给引擎',
    );
    expect(shell.graph.nodes.length, greaterThan(before), reason: '副本没出现在画布上');

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyS);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    expect(transport.calls.any((c) => c.$1 == 'flow.save'), isTrue);
  });

  testWidgets('Tab 整理、右键点线即断', (tester) async {
    final (shell, transport) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('palette-math.add')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    final src = shell.graph.nodes.first;
    final dst = shell.graph.nodes.last;
    // 先把两张卡拉开：线中段才落在空白处（卡片自己的手势会抢走那一下）。
    dst.position = dst.position + const Offset(320, 180);
    dst.touch();
    await tester.pump(const Duration(milliseconds: 320));
    await tester.pump();

    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    expect(
      transport.calls.any((c) => c.$1 == 'flow.nodes_move'),
      isTrue,
      reason: 'Tab 没触发整理',
    );

    expect(
      shell.graph
          .connect(PortRef(src.id, outputPortId(0)), PortRef(dst.id, inputPortId(0)))
          .ok,
      isTrue,
    );
    await tester.pump(const Duration(milliseconds: 320));
    await tester.pump();
    expect(shell.graph.edges, isNotEmpty);

    // 线细得点不准，所以右键落在线上任何一处都直接断掉它。
    final edge = shell.graph.edges.first;
    final geo = shell.graph.edgeGeometry(edge)!;
    // 取曲线正中那一点（直线中点可能离曲线十几像素，落在判定半径外）。
    final origin = tester.getRect(find.byKey(const Key('canvas'))).topLeft;
    final mid = origin + shell.graph.toView(geo.sample(20)[10]);
    await tester.tapAt(mid, buttons: kSecondaryButton);
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    expect(shell.graph.edges.any((e) => e.id == edge.id), isFalse,
        reason: '右键点线没断掉它');
  });

  testWidgets('配置档那一排能新建、改名与删除', (tester) async {
    final (_, transport) = await bootFlow(tester);

    await tester.tap(find.byKey(const ValueKey('profile-kit-new')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(transport.calls.any((c) => c.$1 == 'flow.profile_new'), isTrue);
    expect(find.text('配置1'), findsWidgets, reason: '新建的配置没进下拉');

    await tester.tap(find.byKey(const ValueKey('profile-kit-rename')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.enterText(
      find.byKey(const ValueKey('profile-kit-name-field')),
      '日常',
    );
    await tester.tap(find.text('确定'));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    final renamed = transport.calls
        .lastWhere((c) => c.$1 == 'flow.profile_rename');
    expect(renamed.$2['old'], '配置1');
    expect(renamed.$2['new'], '日常');
    expect(find.text('日常'), findsWidgets);

    await tester.tap(find.byKey(const ValueKey('profile-kit-delete')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.tap(find.text('删除'));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(
      transport.calls.any((c) => c.$1 == 'flow.profile_remove'),
      isTrue,
      reason: '删除没交给引擎',
    );
    expect(find.text('日常'), findsNothing, reason: '删掉的配置还挂在按钮上');
    expect(find.text('默认'), findsWidgets, reason: '删掉当前这份后没有切回剩下那份');
  });

  // 失效判断按「执行流走不走得到」：接到驱动上的那张不该被认错，走不到的才灰。
  testWidgets('执行流走不到的卡片灰显，接上执行流后灰显与注释一起收掉',
      (tester) async {
    final (shell, transport) = await bootFlow(tester);
    // 空画布上没有卡片，FlowScope 要等有卡了才建得出来：先随便落一张再拿编辑器。
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    final editor = tester.widget<FlowScope>(find.byType(FlowScope).first).editor;
    await editor.clearPage();
    await editor.addCard('driver.period', const Offset(0, 0));
    await editor.addCard('core.strength', const Offset(320, 0));
    await editor.addCard('core.strength', const Offset(320, 180));
    final driver = shell.graph.nodes[0].id;
    final wired = shell.graph.nodes[1].id;
    final idleCard = shell.graph.nodes[2].id;
    await editor.setParam(wired, 'channel', 'A');
    await editor.setParam(idleCard, 'channel', 'A');
    await editor.connect(PortRef(driver, 'out:0'), PortRef(wired, 'exec'));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    expect(editor.state.idle, {idleCard}, reason: '接不到执行流的那张该灰显');
    expect(find.byKey(ValueKey('idle-veil-$idleCard')), findsOneWidget);
    expect(find.byKey(ValueKey('idle-veil-$wired')), findsNothing);
    expect(
      find.descendant(
        of: find.byType(FlowNodeView),
        matching: find.text('无连线'),
      ),
      findsOneWidget,
      reason: '卡片上只该显示分类短标签',
    );
    // 标签要在整张卡的正中：放在边上会被邻卡与卡片自己的内容挡住（用户退回过一次）。
    final card = tester.getRect(find.byKey(ValueKey('pos-$idleCard')));
    final tag = tester.getRect(
      find.descendant(
        of: find.byType(FlowNodeView),
        matching: find.text('无连线'),
      ),
    );
    expect(
      (tag.center - card.center).distance,
      lessThan(3),
      reason: '标签要上下左右都居中在卡片里，实际偏了 ${tag.center - card.center}',
    );
    expect(
      find.textContaining('没有执行流走到'),
      findsNothing,
      reason: '完整那句只写进引擎日志，不铺在卡片上',
    );

    await editor.connect(PortRef(driver, 'out:0'), PortRef(idleCard, 'exec'));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    expect(editor.state.idle, isEmpty, reason: '接上执行流后不该继续灰显');
    expect(find.byKey(ValueKey('idle-veil-$idleCard')), findsNothing);

    // 界面那份旧快照还挂着这张卡的失效标记：按秒拉的实时值现在一起带判断结果，
    // 会把卡片洗回正常样子，不用重进这一页。
    editor.state = editor.state.copyWith(idle: {idleCard});
    await tester.pump(const Duration(milliseconds: 700));
    await tester.pump();
    expect(
      transport.calls.any((c) => c.$1 == 'flow.live'),
      isTrue,
      reason: '这页没在拉实时值',
    );
    expect(editor.state.idle, isEmpty, reason: '实时值那一轮没把灰显收敛回来');
    expect(editor.state.errors, isEmpty, reason: '实时值那一轮没把红字收敛回来');
  });

  // 冲突与「无连线」是两回事：撞在同一个参数上的那几张要一起灰、一起报冲突。
  testWidgets('同一执行流上两张卡写不同值：两张都灰显并报写入冲突',
      (tester) async {
    final (shell, _) = await bootFlow(tester);
    await tester.tap(find.byKey(const ValueKey('palette-var.const_float')));
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();
    final editor = tester.widget<FlowScope>(find.byType(FlowScope).first).editor;
    await editor.clearPage();
    await editor.addCard('driver.period', const Offset(0, 0));
    await editor.addCard('core.strength', const Offset(320, 0));
    await editor.addCard('core.strength', const Offset(320, 180));
    final driver = shell.graph.nodes[0].id;
    final low = shell.graph.nodes[1].id;
    final high = shell.graph.nodes[2].id;
    for (final id in [low, high]) {
      await editor.setParam(id, 'channel', 'A');
      await editor.connect(PortRef(driver, 'out:0'), PortRef(id, 'exec'));
    }
    await editor.setOverride(low, 0, 0);
    await editor.setOverride(high, 0, 1);
    await tester.pump(const Duration(milliseconds: 260));
    await tester.pump();

    expect(editor.state.idle, {low, high}, reason: '抢同一个参数的两张都该灰显');
    expect(find.byKey(ValueKey('idle-veil-$low')), findsOneWidget);
    expect(find.byKey(ValueKey('idle-veil-$high')), findsOneWidget);
    expect(
      find.descendant(
        of: find.byType(FlowNodeView),
        matching: find.textContaining('写入冲突'),
      ),
      findsNWidgets(2),
    );
    expect(
      find.descendant(
        of: find.byType(FlowNodeView),
        matching: find.textContaining('无连线'),
      ),
      findsNothing,
      reason: '两张都接在执行流上，不该报成没连线',
    );
  });
}
