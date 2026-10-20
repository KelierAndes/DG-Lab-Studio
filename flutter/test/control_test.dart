import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/rendering.dart' show RenderParagraph;
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:dgstudio/app/shell.dart';
import 'package:dgstudio/chrome/charts.dart';
import 'package:dgstudio/chrome/primitives.dart';
import 'package:dgstudio/engine/engine_state.dart';
import 'package:dgstudio/engine/engine_transport.dart';
import 'package:dgstudio/pages/app_pages.dart';
import 'package:dgstudio/pages/binding_pad.dart';

import 'harness.dart';

void main() {
  Future<(AppShellState, MockEngineTransport)> bootControl(
    WidgetTester tester,
    String slotId,
  ) async {
    final transport = MockEngineTransport(heartbeat: false);
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
      // 每台设备一张卡，负鼠的映射机身在最下面；窗口给足高度，测的是行为不是滚动。
      size: const Size(1320, 2400),
    );
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.control);
    shell.nav.selectSlot(slotId);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump();
    return (shell, transport);
  }

  testWidgets('每台设备一张控制卡，通道写明白', (tester) async {
    await bootControl(tester, 'slot_1');
    for (final id in ['slot_1', 'slot_2', 'slot_3']) {
      expect(find.byKey(ValueKey('device-card-$id')), findsOneWidget);
    }
    expect(find.text('郊狼 (电刺激) · COYOTE-V4   连接通道: Socket V4'), findsOneWidget);
    expect(find.text('灵猫 (气压) · BMTR-01   连接通道: Socket V4'), findsOneWidget);
    expect(find.text('负鼠 (振动) · OVC-3   连接通道: 蓝牙直连'), findsOneWidget);
  });

  testWidgets('输出卡带本卡参数、A/B 两行与实时波形图', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_1');
    final card = find.byKey(const ValueKey('device-card-slot_1'));
    for (final key in [
      'param-max_strength',
      'param-fire_strength_a',
      'param-strength_step',
      'strength-plus-A',
      'wave-pick-A',
    ]) {
      expect(
        find.descendant(of: card, matching: find.byKey(ValueKey(key))),
        findsOneWidget,
        reason: key,
      );
    }
    expect(
      find.descendant(of: card, matching: find.text('实时输出波形')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: card, matching: find.byType(WaveChart)),
      findsOneWidget,
    );

    // 加减步长由引擎给（郊狼 1 步），点一下送 ±1。
    await tester.tap(
      find.descendant(
        of: card,
        matching: find.byKey(const ValueKey('strength-plus-A')),
      ),
    );
    await tester.pump(const Duration(milliseconds: 260));
    final call = transport.calls.lastWhere(
      (c) => c.$1 == 'engine.add_strength',
    );
    expect(call.$2['delta'], 1);
    expect(call.$2['slot_id'], 'slot_1');
  });

  testWidgets('直接设置两个通道后一次应用', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_1');
    final card = find.byKey(const ValueKey('device-card-slot_1'));
    await tester.enterText(
      find.descendant(
        of: card,
        matching: find.byKey(const ValueKey('direct-A')),
      ),
      '80',
    );
    await tester.enterText(
      find.descendant(
        of: card,
        matching: find.byKey(const ValueKey('direct-B')),
      ),
      '40',
    );
    await tester.tap(
      find.descendant(
        of: card,
        matching: find.byKey(const ValueKey('control-apply-direct')),
      ),
    );
    await tester.pump(const Duration(milliseconds: 260));

    final sets = transport.calls
        .where((c) => c.$1 == 'engine.set_strength')
        .toList();
    expect(
      sets.map((c) => '${c.$2['channel']}=${c.$2['value']}'),
      containsAll(['A=80', 'B=40']),
    );
  });

  testWidgets('灵猫卡是只读卡：气压读数 + 折线图 + 清零翻转', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_2');
    final card = find.byKey(const ValueKey('device-card-slot_2'));
    expect(
      find.descendant(of: card, matching: find.textContaining('气压:')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: card, matching: find.byType(PressureChart)),
      findsOneWidget,
    );
    expect(
      find.descendant(of: card, matching: find.text('边控状态: --')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: card, matching: find.text('归零')),
      findsNothing,
      reason: '灵猫不下发强度',
    );

    await tester.tap(find.byKey(const ValueKey('sensor-reset-pressure')));
    await tester.pump(const Duration(milliseconds: 260));
    expect(transport.calls.any((c) => c.$1 == 'engine.reset_pressure'), isTrue);
  });

  // 按键映射只对负鼠存在：旧界面按 family 判定，这里同样按设备型号。
  // 用户要求控制页真的换个版式，不是给旧表单加标题。
  testWidgets('宽窗口下设备卡分左右两栏', (tester) async {
    await bootControl(tester, 'slot_1');
    // 页面上有郊狼与负鼠两张输出卡，必须限定在一张里量。
    final card = find.byKey(const ValueKey('device-card-slot_1'));
    final left = tester.getRect(
      find.descendant(of: card, matching: find.text('本卡参数')),
    );
    final right = tester.getRect(
      find.descendant(of: card, matching: find.text('实时输出波形')),
    );
    expect(right.left, greaterThan(left.right), reason: '「参数」在左栏，「波形 / 触发」在右栏');
    expect((right.top - left.top).abs(), lessThan(40.0), reason: '两栏顶部大致齐平');
    // 通道那一行最宽，排在两栏之上、独占整幅。
    final chan = tester.getRect(
      find.descendant(of: card, matching: find.text('输出通道')),
    );
    expect(chan.top, lessThan(left.top), reason: '通道行要排在参数栏与波形栏之上');
    expect(chan.left, closeTo(left.left, 1.0), reason: '通道行与左栏同一起点（不缩在半栏里）');
  });

  testWidgets('每通道行自带脉冲开火与持续开火，LED 与直接设置同行', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_3');
    final card = find.byKey(const ValueKey('device-card-slot_3'));
    for (final ch in const ['A', 'B']) {
      final zero = find.descendant(
        of: card,
        matching: find.byKey(ValueKey('strength-zero-$ch')),
      );
      final fire = find.descendant(
        of: card,
        matching: find.byKey(ValueKey('control-fire-$ch')),
      );
      final hold = find.descendant(
        of: card,
        matching: find.byKey(ValueKey('control-hold-$ch')),
      );
      expect(fire, findsOneWidget, reason: '$ch 通道要有脉冲开火');
      expect(hold, findsOneWidget, reason: '$ch 通道要有持续开火');
      expect(
        (tester.getCenter(zero).dy - tester.getCenter(fire).dy).abs(),
        lessThan(6),
        reason: '$ch 的开火键要和通道同一行',
      );
    }

    await tester.tap(
      find.descendant(
        of: card,
        matching: find.byKey(const ValueKey('control-fire-B')),
      ),
    );
    await tester.pump(const Duration(milliseconds: 260));
    expect(
      transport.calls.lastWhere((c) => c.$1 == 'engine.fire').$2['channel'],
      'B',
    );

    final gesture = await tester.startGesture(
      tester.getCenter(
        find.descendant(
          of: card,
          matching: find.byKey(const ValueKey('control-hold-A')),
        ),
      ),
    );
    await tester.pump();
    expect(
      transport.calls
          .lastWhere((c) => c.$1 == 'engine.fire_start')
          .$2['channel'],
      'A',
    );
    await gesture.up();
    await tester.pump(const Duration(milliseconds: 220));
    expect(
      transport.calls
          .lastWhere((c) => c.$1 == 'engine.fire_stop')
          .$2['channel'],
      'A',
    );

    // LED 颜色并进「直接设置强度」那一排，不再单独占一栏。
    final led = find.descendant(
      of: card,
      matching: find.byKey(const ValueKey('led-slot_3')),
    );
    final apply = find.descendant(
      of: card,
      matching: find.byKey(const ValueKey('control-apply-direct')),
    );
    expect(
      (tester.getCenter(led).dy - tester.getCenter(apply).dy).abs(),
      lessThan(9),
      reason: 'LED 设置要与直接设置强度同行',
    );
    expect(
      find.descendant(of: card, matching: find.text('设备设置')),
      findsNothing,
      reason: '原来的「设备设置」整栏已经合进这一排',
    );
  });

  testWidgets('蓝牙直连的灵猫：LED 颜色并进传感器操作那一行', (tester) async {
    final transport = MockEngineTransport(heartbeat: false);
    transport.slotChannels['slot_2'] = 'ble';
    final engine = StudioEngine(transport);
    addTearDown(engine.dispose);
    final shell = await bootShell<AppShellState>(
      tester,
      AppShell(engine: engine),
      size: const Size(1320, 2400),
    );
    await tester.pump(const Duration(milliseconds: 120));
    shell.nav.go(AppPage.control);
    shell.nav.selectSlot('slot_2');
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump();

    final card = find.byKey(const ValueKey('device-card-slot_2'));
    final led = find.descendant(
      of: card,
      matching: find.byKey(const ValueKey('led-slot_2')),
    );
    expect(led, findsOneWidget, reason: '蓝牙直连的灵猫要能设机身 LED 颜色');
    final flip = find.descendant(
      of: card,
      matching: find.byKey(const ValueKey('sensor-flip')),
    );
    expect(
      (tester.getCenter(led).dy - tester.getCenter(flip).dy).abs(),
      lessThan(9),
      reason: 'LED 颜色要和「气压清零 / 翻转屏幕」同一行，省下整栏高度',
    );
    expect(
      find.descendant(of: card, matching: find.text('机身 LED 颜色')),
      findsNothing,
      reason: '单独一栏的「设备设置 · 机身 LED 颜色」已经收掉',
    );
  });

  testWidgets('LED 颜色：切完回显选中项，文字后面带同色圆点', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_3');
    final picker = find.byKey(const ValueKey('led-slot_3'));
    expect(
      find.descendant(of: picker, matching: find.text('熄灭')),
      findsOneWidget,
      reason: '没设过的设备默认显示「熄灭」',
    );

    // 直接驱动弹层：测试窗口里像素点击的命中区不稳。
    tester.widget<PickerField>(picker).onChanged('紫色');
    await tester.pump(const Duration(milliseconds: 260));
    final call = transport.calls.lastWhere(
      (c) => c.$1 == 'engine.set_led_color',
    );
    expect(call.$2['color'], 'purple');
    expect(call.$2['slot_id'], 'slot_3');
    expect(
      find.descendant(of: picker, matching: find.text('紫色')),
      findsOneWidget,
      reason: '机身颜色变了，下拉却还停在「熄灭」就是没回显',
    );
    final field = tester.widget<PickerField>(picker);
    expect(field.colors, isNotNull, reason: '颜色选项要带色点');
    expect(field.colors!.length, field.options.length);
    // 选中项后面那颗圆点：按当前选项取色。
    final dot = find.descendant(
      of: picker,
      matching: find.byWidgetPredicate(
        (w) =>
            w is Container &&
            w.decoration is BoxDecoration &&
            (w.decoration as BoxDecoration).shape == BoxShape.circle,
      ),
    );
    expect(dot, findsOneWidget);
  });

  testWidgets('绑定面板：框随文字加宽，序号角标已经去掉', (tester) async {
    await bootControl(tester, 'slot_3');
    final long = tester.getRect(find.byKey(const ValueKey('binding-13')));
    final short = tester.getRect(find.byKey(const ValueKey('binding-2')));
    expect(long.width, greaterThan(short.width), reason: '长名字要加宽框，不能截成省略号');
    // 位号角标既看不清也不对应什么可操作的东西，用户要求去掉（别再挂回来）。
    expect(find.byKey(const ValueKey('binding-badge-13')), findsNothing);

    // 两列标签靠机身那侧的边钉在「机身缘 + 固定间隔」上，不再整列推到画布两端。
    final pad = tester.getRect(find.byType(BindingPad));
    final k = pad.width / 1040;
    final leftCol = tester.getRect(find.byKey(const ValueKey('binding-8')));
    final rightCol = tester.getRect(find.byKey(const ValueKey('binding-14')));
    expect(
      (leftCol.right - pad.left) / k,
      closeTo(311, 1),
      reason: '左列右缘要贴在机身左缘 +16 的位置',
    );
    expect(
      (rightCol.left - pad.left) / k,
      closeTo(727, 1),
      reason: '右列左缘要贴在机身右缘 +16 的位置',
    );
  });

  test('引线折点：水平段走标签自己的高度，第二段是 45°', () {
    // 先斜再平会让多条线在键那一侧共用同一段水平线（用户看到的「引导线重叠」）。
    for (final (start, to) in [
      (const Offset(288, 39), const Offset(414, 90)),
      (const Offset(288, 94.5), const Offset(375, 130)),
      (const Offset(288, 150), const Offset(453, 130)),
      (const Offset(740, 94.5), const Offset(620, 130)),
      (const Offset(519, 255), const Offset(519, 195)),
    ]) {
      final knee = BindingPad.leadKnee(start, to);
      expect(knee.dy, start.dy, reason: '水平段必须停在标签高度上');
      final dir = to.dx >= start.dx ? 1 : -1;
      expect(
        (knee.dx - start.dx) * dir,
        greaterThanOrEqualTo(0),
        reason: '折点不能跑到起点后面（会画成回头线）',
      );
      final vertical = (to.dx - start.dx).abs() < .001;
      if (vertical) {
        // 正上 / 正下方的键（HOME 那颗）没有水平段可走，直接一条竖线。
        expect(knee, start);
      } else {
        expect(
          (to.dx - knee.dx).abs(),
          closeTo((to.dy - knee.dy).abs(), .001),
          reason: '第二段必须正好是 45°',
        );
      }
    }
  });

  testWidgets('负鼠机身可视化绑定：点按键改动作并写回', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_3');
    expect(find.text('负鼠按键映射'), findsOneWidget);
    expect(
      find.text('持续开火 A (按住，仅 A 通道)'),
      findsOneWidget,
      reason: 'A 键的现有绑定没有从引擎读出来',
    );
    // 标签宁可缩字号也不能省略：截成「A 通道强…」就看不出这颗键绑的是什么。
    final label = tester.renderObject(
      find
          .descendant(
            of: find.byKey(const ValueKey('binding-13')),
            matching: find.byType(RichText),
          )
          .first,
    ) as RenderParagraph;
    expect(
      label.size.width,
      moreOrLessEquals(
        (TextPainter(
          text: label.text,
          textDirection: TextDirection.ltr,
          maxLines: 1,
        )..layout()).width,
        epsilon: 1,
      ),
      reason: '按键标签被截断成「A 通道强…」就读不出绑的是什么了',
    );

    await tester.tap(find.byKey(const ValueKey('binding-13')));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('绑到什么动作'), findsOneWidget);

    await tester.tap(find.text('急停'));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();

    final call = transport.calls.lastWhere((c) => c.$1 == 'bindings.set');
    expect(call.$2['bit'], '13');
    expect(call.$2['action'], 'estop');
    expect(find.text('急停'), findsOneWidget, reason: '改完没回显到机身按键上');
  });

  // 键盘与 OSC 这类「带参数」的动作：旧界面能按一下就捕获键、能填 OSC 路径，
  // 这两条路以前没搬过来，绑出来的值就只剩半个。
  testWidgets('模拟键盘动作：按下按键即捕获并写成 key:<键名>', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_3');
    await tester.tap(find.byKey(const ValueKey('binding-13')));
    await tester.pump(const Duration(milliseconds: 300));
    expect(
      tester.getTopLeft(find.text('模拟键盘按键…')).dy,
      greaterThan(tester.getTopLeft(find.text('键盘与模块动作')).dy),
      reason: '键盘注入要列在「键盘与模块动作」那一组，不是设备动作组',
    );
    await tester.tap(find.text('模拟键盘按键…'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('点击此处后按下要绑定的按键'), findsOneWidget);

    await tester.sendKeyDownEvent(LogicalKeyboardKey.space);
    await tester.pump(const Duration(milliseconds: 120));
    expect(find.textContaining('Space'), findsWidgets, reason: '按下的键要立刻显示出来');

    await tester.tap(find.text('确定'));
    await tester.pump(const Duration(milliseconds: 300));
    final call = transport.calls.lastWhere((c) => c.$1 == 'bindings.set');
    expect(call.$2['action'], 'key:Space');
  });

  testWidgets('OSC 动作：填参数后写成 osc:<路径>', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_3');
    await tester.tap(find.byKey(const ValueKey('binding-2')));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('设备动作'), findsOneWidget);
    expect(
      find.text('键盘与模块动作'),
      findsOneWidget,
      reason: '键盘注入与模块动作要单列一组：混在长列表尾巴上，用户就当没搬过来',
    );
    await tester.tap(find.text('发送 OSC 参数'));
    await tester.pump(const Duration(milliseconds: 300));

    await tester.enterText(
      find.byKey(const ValueKey('binding-arg')),
      '/avatar/parameters/Stim',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 300));

    final call = transport.calls.lastWhere((c) => c.$1 == 'bindings.set');
    expect(call.$2['bit'], '2');
    expect(call.$2['action'], 'osc:/avatar/parameters/Stim');
  });

  testWidgets('只有负鼠那张卡带机身绑定，郊狼卡没有', (tester) async {
    await bootControl(tester, 'slot_1');
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('device-card-slot_1')),
        matching: find.text('负鼠按键映射'),
      ),
      findsNothing,
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('device-card-slot_3')),
        matching: find.text('负鼠按键映射'),
      ),
      findsOneWidget,
    );
  });

  testWidgets('映射配置档可以新建、改名与删除', (tester) async {
    final (_, transport) = await bootControl(tester, 'slot_3');
    await tester.tap(find.byKey(const ValueKey('profile-kit-new')));
    await tester.pump(const Duration(milliseconds: 300));
    expect(transport.calls.any((c) => c.$1 == 'bindings.profile_new'), isTrue);
    expect(find.text('映射 2'), findsOneWidget, reason: '新配置档没出现在下拉里');

    // 改名：对话框里那格默认带着当前名字，敲新名点确定就交给引擎。
    await tester.tap(find.byKey(const ValueKey('profile-kit-rename')));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.enterText(
      find.byKey(const ValueKey('profile-kit-name-field')),
      '战斗映射',
    );
    await tester.tap(find.text('确定'));
    await tester.pump(const Duration(milliseconds: 300));
    final renamed = transport.calls.lastWhere((c) => c.$1 == 'bindings.profile_rename');
    expect(renamed.$2['name'], '映射 2');
    expect(renamed.$2['new'], '战斗映射');
    expect(find.text('战斗映射'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('profile-kit-delete')));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.text('删除'));
    await tester.pump(const Duration(milliseconds: 300));
    final removed = transport.calls
        .lastWhere((c) => c.$1 == 'bindings.profile_remove');
    expect(removed.$2['name'], '战斗映射');
    expect(find.text('战斗映射'), findsNothing, reason: '删掉的配置档还留在下拉里');
  });
}
