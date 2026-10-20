import 'dart:async';

import 'package:flutter/material.dart' hide Stack;

import '../app/tokens.dart';
import '../chrome/charts.dart';
import '../chrome/device_icons.dart';
import '../chrome/nav_rail.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../engine/engine_models.dart';
import '../engine/engine_state.dart';
import 'actions.dart';
import 'app_pages.dart';
import 'binding_card.dart';

/// 控制：每台设备一张控制卡片——本卡参数、A/B 两路强度与波形、实时柱状波形图。
///
/// 布局照旧界面：郊狼 / 负鼠走输出卡，灵猫是传感卡（只读气压与边控状态，
/// 不下发任何强度或波形）。所有卡片按内容平铺，卡片内部不再套滚动条。
class ControlPage extends StatelessWidget {
  const ControlPage({
    super.key,
    required this.nav,
    required this.engine,
    required this.actions,
  });

  final AppNav nav;
  final StudioEngine engine;
  final StudioActions actions;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: Listenable.merge([nav, engine]),
      builder: (context, _) {
        final slots = [...engine.snapshot.slots]
          ..sort((a, b) => a.slotId.compareTo(b.slotId));
        return PageBody(
          pages: nav,
          header: PageHeader(
            title: '控制',
            subtitle: '每台设备一张控制卡片：输出强度、波形选择与实时柱状波形图',
            pages: nav,
            search: false,
            actions: [
              // 右上角只留急停：其余动作回到正文里，别让红键淹在一排胶囊中间。
              EstopButton(
                key: const ValueKey('control-estop'),
                onTap: () => actions.invoke('engine.emergency_stop'),
              ),
            ],
          ),
          children: [
            if (slots.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Row(
                  children: [
                    TinyLabel(
                      '所有卡片都是本机这台设备的参数',
                      color: t.textTertiary,
                      size: 9.5,
                    ),
                    const Spacer(),
                    OptionPill(
                      key: const ValueKey('control-clear-all'),
                      label: '全部归零',
                      active: false,
                      dense: true,
                      onTap: () => actions.invoke('engine.clear_wave'),
                    ),
                  ],
                ),
              ),
            if (slots.isEmpty)
              BentoCard(
                fit: true,
                title: '控制',
                accent: t.portPositive,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
                  child: Row(
                    children: [
                      const Expanded(
                        child: EmptyNote('尚未接入设备：先在「连接」页接入后再回来控制。'),
                      ),
                      OptionPill(
                        key: const ValueKey('control-goto-connect'),
                        label: '前往连接页',
                        active: true,
                        dense: true,
                        onTap: () => nav.go(AppPage.connect),
                      ),
                    ],
                  ),
                ),
              ),
            for (final slot in slots)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: _DeviceCard(
                  key: ValueKey('device-card-${slot.slotId}'),
                  slot: slot,
                  engine: engine,
                  actions: actions,
                ),
              ),
          ],
        );
      },
    );
  }
}

class _DeviceCard extends StatefulWidget {
  const _DeviceCard({
    super.key,
    required this.slot,
    required this.engine,
    required this.actions,
  });

  final DeviceSlot slot;
  final StudioEngine engine;
  final StudioActions actions;

  String get family => familyOf(slot.type);

  static String familyOf(String type) {
    final t = type.toUpperCase();
    if (t.startsWith('OVC')) return 'OVC';
    if (t.startsWith('BMTR')) return 'BMTR';
    return 'COYOTE';
  }

  @override
  State<_DeviceCard> createState() => _DeviceCardState();
}

class _DeviceCardState extends State<_DeviceCard> {
  /// 波形柱状图的采样：只在输出设备上轮询，节奏与旧界面一致（0.25s）。
  List<WaveSample> _wave = const [];
  Timer? _poll;

  /// 按住开火是逐通道的：A 在持续开火时 B 也要能各自按住。
  final Set<String> _holding = {};
  final TextEditingController _directA = TextEditingController();
  final TextEditingController _directB = TextEditingController();

  DeviceSlot get slot => widget.slot;
  StudioEngine get engine => widget.engine;
  StudioActions get actions => widget.actions;
  bool get isOvc => _DeviceCard.familyOf(slot.type) == 'OVC';
  bool get isBle => engine.snapshot.slotChannels[slot.slotId] == 'ble';

  @override
  void initState() {
    super.initState();
    unawaited(engine.loadParams(slot.slotId));
    if (slot.isOutputDevice) {
      _poll = Timer.periodic(
        const Duration(milliseconds: 250),
        (_) => _pullWave(),
      );
      unawaited(_pullWave());
    }
  }

  @override
  void dispose() {
    _poll?.cancel();
    _directA.dispose();
    _directB.dispose();
    super.dispose();
  }

  Future<void> _pullWave() async {
    if (!mounted) return;
    final samples = await engine.waveSamples(slot.slotId);
    if (!mounted) return;
    setState(
      () => _wave = [for (final s in samples) (at: s.at, a: s.a, b: s.b)],
    );
  }

  int get _step => engine.deviceSteps[slot.slotId] ?? 1;

  List<Map<String, Object?>> get _params =>
      engine.deviceParams[slot.slotId] ?? const [];

  void _setParam(String key, Object? value) {
    unawaited(
      actions.invoke('engine.set_intensity_param', {
        'key': key,
        'value': value,
        'slot_id': slot.slotId,
      }),
    );
    unawaited(engine.loadParams(slot.slotId));
  }

  void _add(String channel, int sign) => actions.invoke('engine.add_strength', {
    'channel': channel,
    'delta': sign * _step,
    'slot_id': slot.slotId,
  });

  void _applyDirect() {
    final a = int.tryParse(_directA.text.trim());
    final b = int.tryParse(_directB.text.trim());
    if (a != null) {
      actions.invoke('engine.set_strength', {
        'channel': 'A',
        'value': a,
        'slot_id': slot.slotId,
      });
    }
    if (b != null) {
      actions.invoke('engine.set_strength', {
        'channel': 'B',
        'value': b,
        'slot_id': slot.slotId,
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return BentoCard(
      fit: true,
      title: slot.label,
      accent: slot.isOutputDevice ? t.portPositive : t.portModel,
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          StatusChip(
            text: slot.battery == null ? '电量 --' : '电量 ${slot.battery}%',
            tone: (slot.battery ?? 100) > 60 ? 1 : 2,
          ),
          const SizedBox(width: 6),
          StatusChip(text: slot.isOutputDevice ? '2 路输出' : '只监听', tone: 0),
        ],
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _head(t),
            const SizedBox(height: 10),
            if (slot.isOutputDevice) _outputBody(t) else ..._sensorBody(t),
          ],
        ),
      ),
    );
  }

  Widget _head(AppTokens t) => Row(
    children: [
      DeviceTile(
        family: _DeviceCard.familyOf(slot.type),
        size: 30,
        active: true,
      ),
      const SizedBox(width: 9),
      Expanded(
        child: Text(
          '${_familyLabel(slot.type)} · ${slot.type}   连接通道: ${engine.snapshot.channelLabel(slot.slotId)}',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 10.5, height: 1.3, color: t.textTertiary),
        ),
      ),
    ],
  );

  /// 输出设备卡版式：A/B 通道那一行独占整幅（它最宽），下面再分左右两栏——
  /// 左边「这台设备的参数」，右边「实时波形 + 触发 + 设备设置」；负鼠的机身绑定占整幅。
  /// 窄于 820 时全部退回单列。选项一个不少，只是不再从上到下堆成一张长表单。
  Widget _outputBody(AppTokens t) => LayoutBuilder(
    builder: (context, box) {
      final params = <Widget>[
        GroupPanel(
          label: '本卡参数',
          hint: '只对这台设备生效；开火强度 A/B 分通道设置，0 = 跟随本卡上限',
          child: _ParamsGrid(rows: _params, ovc: isOvc, onSet: _setParam),
        ),
      ];
      final channels = <Widget>[
        GroupPanel(
          label: '输出通道',
          hint:
              '强度用加减键调节（步长见本卡参数）；波形可下拉选择或 ‹ / › 逐步切换；'
              '「归零」清强度并切回静默',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final ch in const ['A', 'B'])
                _ChannelRow(
                  key: ValueKey('channel-$ch'),
                  channel: ch,
                  slot: slot,
                  waves: _wavesFor(slot.type),
                  waveName: _waveName(ch),
                  holding: _holding.contains(ch),
                  onStep: (sign) => _add(ch, sign),
                  onSet: (v) => actions.invoke('engine.set_strength', {
                    'channel': ch,
                    'value': v,
                    'slot_id': slot.slotId,
                  }),
                  onWave: (name) => actions.invoke('engine.set_wave', {
                    'channel': ch,
                    'name': name,
                    'slot_id': slot.slotId,
                  }),
                  onZero: () => actions.invoke('engine.reset_strength', {
                    'channel': ch,
                    'slot_id': slot.slotId,
                  }),
                  onFire: () => actions.invoke('engine.fire', {
                    'slot_id': slot.slotId,
                    'channel': ch,
                  }),
                  onHoldStart: () {
                    setState(() => _holding.add(ch));
                    actions.invoke('engine.fire_start', {
                      'slot_id': slot.slotId,
                      'channel': ch,
                    });
                  },
                  onHoldStop: () {
                    setState(() => _holding.remove(ch));
                    actions.invoke('engine.fire_stop', {
                      'slot_id': slot.slotId,
                      'channel': ch,
                    });
                  },
                ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      TinyLabel('A 强度', color: t.textTertiary),
                      const SizedBox(width: 5),
                      _DirectBox(
                        key: const ValueKey('direct-A'),
                        controller: _directA,
                        hint: 'A 强度',
                      ),
                      const SizedBox(width: 8),
                      TinyLabel('B 强度', color: t.textTertiary),
                      const SizedBox(width: 5),
                      _DirectBox(
                        key: const ValueKey('direct-B'),
                        controller: _directB,
                        hint: 'B 强度',
                      ),
                      const SizedBox(width: 8),
                      GhostButton(
                        key: const ValueKey('control-apply-direct'),
                        label: '应用直接设置',
                        dense: true,
                        active: true,
                        onTap: _applyDirect,
                      ),
                    ],
                  ),
                  // LED 颜色并入这一行：设备设置不该再单独占一栏。
                  if (isBle)
                    _LedRow(slot: slot, actions: actions, engine: engine),
                ],
              ),
              const SizedBox(height: 3),
              TinyLabel(
                isOvc ? '直接设置 10 的倍数 (自动取整)，任填其一或同时填写' : '直接设置 0-200，任填其一或同时填写',
                color: t.textTertiary,
                size: 9,
              ),
            ],
          ),
        ),
      ];
      final right = <Widget>[
        GroupPanel(
          label: '实时输出波形',
          hint: 'A / B 各一栏 · 最右侧为最新采样 · 0-100 · 开火键在各通道那一行',
          child: WaveChart(samples: _wave),
        ),
      ];
      final binding = <Widget>[
        // 负鼠的按键映射只对蓝牙直连有意义。
        if (isOvc && isBle) ...[
          const SizedBox(height: 2),
          OvcBindingCard(
            key: ValueKey('binding-pad-${slot.slotId}'),
            engine: engine,
            slotLabel: slot.label,
          ),
        ],
      ];
      if (box.maxWidth < 820) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [...channels, ...params, ...right, ...binding],
        );
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 通道那一行最宽（±、读数、电平条、波形选择、归零），独占整幅；
          // 参数与波形 / 触发并排成两栏，卡片就不再是一条长表单。
          ...channels,
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                flex: 4,
                child: Padding(
                  padding: const EdgeInsets.only(right: 14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: params,
                  ),
                ),
              ),
              Expanded(
                flex: 5,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: right,
                ),
              ),
            ],
          ),
          ...binding,
        ],
      );
    },
  );

  List<Widget> _sensorBody(AppTokens t) => [
    GroupPanel(
      label: '实时读数',
      hint: '灵猫无输出通道：本页不下发任何波形 / 强度指令，仅订阅气压与边控状态',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '气压: ${slot.pressure == null ? '--' : slot.pressure!.toStringAsFixed(2)} kPa',
                style: TextStyle(
                  fontSize: 20,
                  height: 1.1,
                  fontWeight: FontWeight.w600,
                  letterSpacing: -.3,
                  color: t.textPrimary,
                ),
              ),
              const SizedBox(width: 16),
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(
                  '边控状态: ${_edgeLabel(slot.edgeState)}',
                  style: TextStyle(
                    fontSize: 12,
                    height: 1.2,
                    color: t.textSecondary,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          PressureChart(
            samples: [
              for (final p in engine.pressureSamples(slot.slotId))
                (at: p.at, kpa: p.kpa),
            ],
            legend: slot.label,
          ),
        ],
      ),
    ),
    GroupPanel(
      label: '传感器操作',
      child: Row(
        children: [
          Expanded(
            child: GhostButton(
              key: const ValueKey('sensor-reset-pressure'),
              label: '气压清零',
              dense: true,
              onTap: () => actions.invoke('engine.reset_pressure', {
                'slot_id': slot.slotId,
              }),
            ),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: GhostButton(
              key: const ValueKey('sensor-flip'),
              label: '翻转屏幕',
              dense: true,
              onTap: () =>
                  actions.invoke('engine.bmtr_flip', {'slot_id': slot.slotId}),
            ),
          ),
          // LED 颜色与传感器操作同一行：机身设置不该再单独占一栏高度
          // （与负鼠卡「LED 并入直接设置」同一个口径）。
          if (isBle) ...[
            const SizedBox(width: 10),
            _LedRow(slot: slot, actions: actions, engine: engine),
          ],
        ],
      ),
    ),
  ];

  static String _familyLabel(String type) =>
      switch (_DeviceCard.familyOf(type)) {
        'OVC' => '负鼠 (振动)',
        'BMTR' => '灵猫 (气压)',
        _ => '郊狼 (电刺激)',
      };

  static String _edgeLabel(int? state) => switch (state) {
    1 => '刺激',
    2 => '冷静计时',
    3 => '冷静判定',
    4 => '允许高潮',
    0 => '停止',
    _ => '--',
  };

  /// 波形目录按槽位所属家族取，郊狼与负鼠不是同一套。
  List<Map<String, Object?>> _wavesFor(String type) {
    final raw = engine.config['waveforms'];
    if (raw is! Map) return _silentWave;
    final family = _DeviceCard.familyOf(type);
    final list = raw[family == 'BMTR' ? 'COYOTE' : family];
    if (list is List && list.isNotEmpty) {
      return [for (final w in list) Map<String, Object?>.from(w as Map)];
    }
    return _silentWave;
  }

  static const _silentWave = [
    {'name': '__SILENT__', 'label': '静默'},
  ];

  String _waveName(String channel) {
    final selection = engine.config['wave_selection'];
    if (selection is Map) {
      final name = selection[channel];
      if (name is String && name.isNotEmpty) return name;
    }
    return '__SILENT__';
  }
}

/// 本卡参数：上限 / 开火强度 A / 开火强度 B / 加减步长，全部按槽位存。
class _ParamsGrid extends StatelessWidget {
  const _ParamsGrid({
    required this.rows,
    required this.ovc,
    required this.onSet,
  });

  final List<Map<String, Object?>> rows;
  final bool ovc;
  final void Function(String key, Object? value) onSet;

  static const _order = [
    'max_strength',
    'fire_strength_a',
    'fire_strength_b',
    'strength_step',
  ];

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    if (rows.isEmpty) {
      return TinyLabel('正在读取本卡参数…', color: t.textTertiary, size: 9);
    }
    Map<String, Object?>? find(String key) {
      for (final r in rows) {
        if (r['key'] == key) return r;
      }
      return null;
    }

    final shown = [for (final k in _order) ?find(k)];
    // 步进按引擎下发的来：负鼠的整数项一步 10，用 1 去点会被引擎抹回原值。
    int stepOf(Map<String, Object?> r) =>
        ((r['step'] as num?)?.toDouble() ?? 1).round().clamp(1, 100);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 14,
          runSpacing: 8,
          children: [
            for (final r in shown)
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TinyLabel('${r['label']}', color: t.textTertiary, size: 9),
                  const SizedBox(height: 3),
                  StepperField(
                    key: ValueKey('param-${r['key']}'),
                    width: 92,
                    value: (r['value'] as num).round(),
                    onStep: (d) => onSet(
                      '${r['key']}',
                      ((r['value'] as num).round() + d * stepOf(r)).clamp(
                        (r['min'] as num).round(),
                        (r['max'] as num).round(),
                      ),
                    ),
                    onCommit: (v) => onSet(
                      '${r['key']}',
                      v.clamp(
                        (r['min'] as num).round(),
                        (r['max'] as num).round(),
                      ),
                    ),
                  ),
                ],
              ),
          ],
        ),
        const SizedBox(height: 5),
        TinyLabel(
          '仅对本设备生效 · 开火强度 A/B 分通道设置，0 = 跟随本卡上限'
          '${ovc ? ' · OVC 强度按 10 取整' : ''}',
          color: t.textTertiary,
          size: 9,
        ),
      ],
    );
  }
}

class _ChannelRow extends StatelessWidget {
  const _ChannelRow({
    super.key,
    required this.channel,
    required this.slot,
    required this.waves,
    required this.waveName,
    required this.holding,
    required this.onStep,
    required this.onSet,
    required this.onWave,
    required this.onZero,
    required this.onFire,
    required this.onHoldStart,
    required this.onHoldStop,
  });

  final String channel;
  final DeviceSlot slot;
  final List<Map<String, Object?>> waves;
  final String waveName;
  final bool holding;
  final ValueChanged<int> onStep;
  final ValueChanged<int> onSet;
  final ValueChanged<String> onWave;
  final VoidCallback onZero;
  final VoidCallback onFire;
  final VoidCallback onHoldStart;
  final VoidCallback onHoldStop;

  String get _label {
    for (final w in waves) {
      if (w['name'] == waveName) return '${w['label']}';
    }
    return waveName;
  }

  void _stepWave(int dir) {
    if (waves.isEmpty) return;
    final index = waves.indexWhere((w) => w['name'] == waveName);
    final next = ((index < 0 ? 0 : index) + dir).clamp(0, waves.length - 1);
    onWave('${waves[next]['name']}');
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final value = slot.strength[channel] ?? 0;
    final limit = slot.strengthLimit[channel] ?? 200;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          IconSquare(
            key: ValueKey('strength-minus-$channel'),
            icon: Icons.remove_rounded,
            size: 28,
            iconSize: 14,
            onTap: () => onStep(-1),
          ),
          const SizedBox(width: 4),
          IconSquare(
            key: ValueKey('strength-plus-$channel'),
            icon: Icons.add_rounded,
            size: 28,
            iconSize: 14,
            onTap: () => onStep(1),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 74,
            child: Text(
              '$channel: $value/$limit',
              style: TextStyle(
                fontSize: 12.5,
                height: 1.2,
                fontWeight: FontWeight.w600,
                color: t.textPrimary,
              ),
            ),
          ),
          SizedBox(
            width: 92,
            height: 14,
            child: _Meter(
              ratio: limit == 0 ? 0 : value / limit,
              tint: channel == 'A' ? t.portPositive : t.portImage,
            ),
          ),
          const SizedBox(width: 12),
          TinyLabel('波形', color: t.textTertiary),
          const SizedBox(width: 5),
          IconSquare(
            key: ValueKey('wave-prev-$channel'),
            icon: Icons.chevron_left_rounded,
            size: 26,
            iconSize: 13,
            onTap: () => _stepWave(-1),
          ),
          const SizedBox(width: 4),
          PickerField(
            key: ValueKey('wave-pick-$channel'),
            value: _label,
            options: [for (final w in waves) '${w['label']}'],
            width: 132,
            compact: true,
            onChanged: (label) {
              final hit = waves.firstWhere(
                (w) => '${w['label']}' == label,
                orElse: () => const {'name': ''},
              );
              if ('${hit['name']}'.isNotEmpty) onWave('${hit['name']}');
            },
          ),
          const SizedBox(width: 4),
          IconSquare(
            key: ValueKey('wave-next-$channel'),
            icon: Icons.chevron_right_rounded,
            size: 26,
            iconSize: 13,
            onTap: () => _stepWave(1),
          ),
          const SizedBox(width: 8),
          GhostButton(
            key: ValueKey('strength-zero-$channel'),
            label: '归零',
            dense: true,
            onTap: onZero,
          ),
          // 每通道各带一颗脉冲开火与一颗持续开火（旧版 A/B 两通道都是这样）。
          const SizedBox(width: 10),
          GhostButton(
            key: ValueKey('control-fire-$channel'),
            label: '开火 $channel',
            dense: true,
            active: true,
            onTap: onFire,
          ),
          const SizedBox(width: 6),
          _HoldFire(
            key: ValueKey('control-hold-$channel'),
            label: '持续开火 $channel',
            holding: holding,
            onStart: onHoldStart,
            onStop: onHoldStop,
          ),
        ],
      ),
    );
  }
}

class _Meter extends StatelessWidget {
  const _Meter({required this.ratio, required this.tint});

  final double ratio;
  final Color tint;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return LayoutBuilder(
      builder: (context, box) {
        final filled = (box.maxWidth * ratio.clamp(0, 1)).clamp(
          6.0,
          box.maxWidth,
        );
        return Container(
          decoration: BoxDecoration(
            color: t.wellDeep,
            borderRadius: BorderRadius.circular(4),
            border: Border.all(color: Colors.black.withAlpha(110)),
            boxShadow: t.inset,
          ),
          child: Padding(
            padding: const EdgeInsets.all(2),
            child: Align(
              alignment: Alignment.centerLeft,
              child: FractionallySizedBox(
                widthFactor: (filled / box.maxWidth).clamp(0.0, 1.0),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    color: tint,
                    borderRadius: BorderRadius.circular(3),
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

class _DirectBox extends StatelessWidget {
  const _DirectBox({super.key, required this.controller, required this.hint});

  final TextEditingController controller;
  final String hint;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: 120,
    child: InlineField(
      key: key,
      controller: controller,
      hint: hint,
      numeric: true,
      minWidth: 120,
    ),
  );
}

/// 按住开火：按下即开始，松手（含指针丢失）即停。
class _HoldFire extends StatelessWidget {
  const _HoldFire({
    super.key,
    required this.label,
    required this.holding,
    required this.onStart,
    required this.onStop,
  });

  final String label;
  final bool holding;
  final VoidCallback onStart;
  final VoidCallback onStop;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (_) => onStart(),
        onTapUp: (_) => onStop(),
        onTapCancel: onStop,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 90),
          height: 28,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            color: holding ? t.portNegative.withAlpha(60) : t.pill,
            borderRadius: BorderRadius.circular(AppTokens.radiusPill),
            border: Border.all(color: holding ? t.portNegative : t.pillBorder),
          ),
          child: Text(
            holding ? '开火中… (放开停止)' : label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 10,
              height: 1.1,
              letterSpacing: .1,
              fontWeight: FontWeight.w600,
              color: holding ? t.portNegative : t.textSecondary,
            ),
          ),
        ),
      ),
    );
  }
}

class _LedRow extends StatelessWidget {
  const _LedRow({
    required this.slot,
    required this.actions,
    required this.engine,
  });

  final DeviceSlot slot;
  final StudioActions actions;
  final StudioEngine engine;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final options = [
      for (final o in (engine.config['led_options'] as List? ?? const []))
        if (o is Map) Map<String, Object?>.from(o),
    ];
    if (options.isEmpty) return const SizedBox.shrink();
    final byKey = {for (final o in options) '${o['key']}': o};
    // 引擎回传的是键名（off / yellow / …）；没设过的设备按「熄灭」显示。
    final current =
        byKey['${slot.props['led'] ?? ''}'] ?? byKey['off'] ?? options.first;
    return Row(
      // 贴着自己内容的宽度：占满整行的话，Wrap 会把它挤到下一排，
      // 「LED 与直接设置同行」就落空了。
      mainAxisSize: MainAxisSize.min,
      children: [
        TinyLabel('LED 颜色', color: t.textTertiary),
        const SizedBox(width: 8),
        PickerField(
          key: ValueKey('led-${slot.slotId}'),
          width: 110,
          compact: true,
          value: '${current['label']}',
          options: [for (final o in options) '${o['label']}'],
          colors: [for (final o in options) _ledColor('${o['color']}')],
          onChanged: (label) {
            final hit = options.firstWhere(
              (o) => '${o['label']}' == label,
              orElse: () => const {'key': ''},
            );
            if ('${hit['key']}'.isNotEmpty) {
              actions.invoke('engine.set_led_color', {
                'color': hit['key'],
                'slot_id': slot.slotId,
              });
            }
          },
        ),
      ],
    );
  }
}

/// 桥回传的色值是 `#RRGGBB` 文本，直接当颜色用。
Color _ledColor(String hex) => Color(
  0xFF000000 | (int.tryParse(hex.replaceFirst('#', ''), radix: 16) ?? 0x9AA0A6),
);
