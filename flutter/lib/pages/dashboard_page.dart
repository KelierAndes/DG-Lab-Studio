import 'dart:async';

import 'package:flutter/material.dart' hide Stack;

import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../engine/engine_models.dart';
import '../engine/engine_state.dart';
import 'actions.dart';
import 'app_pages.dart';

/// 概览：三条协议各自连没连、连上来几台；每台在跑什么强度 / 气压、电量与当前波形；
/// 开了哪些模块；最近十行日志。
class DashboardPage extends StatefulWidget {
  const DashboardPage({
    super.key,
    required this.nav,
    required this.engine,
    required this.actions,
  });

  final AppNav nav;
  final StudioEngine engine;
  final StudioActions actions;

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  /// 事件流里连好的「模块 ↔ 核心」线路：判断在引擎那侧，这里按几秒取一次清单。
  List<Map<String, Object?>> _lines = const [];
  Timer? _flowTimer;

  StudioEngine get engine => widget.engine;
  AppNav get nav => widget.nav;

  @override
  void initState() {
    super.initState();
    _flowTimer = Timer.periodic(
      const Duration(milliseconds: 2500),
      (_) => _pullFlow(),
    );
    _pullFlow();
  }

  @override
  void dispose() {
    _flowTimer?.cancel();
    super.dispose();
  }

  Future<void> _pullFlow() async {
    final rows = await engine.flowLines();
    if (!mounted) return;
    setState(() => _lines = rows);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: Listenable.merge([nav, engine]),
      builder: (context, _) {
        final s = engine.snapshot;
        final devices = [
          for (final slot in s.slots)
            if (nav.matches(slot.label)) slot,
        ];
        final enabled = engine.modules.where((m) => m.enabled).toList();
        return PageBody(
          pages: nav,
          header: PageHeader(
            title: '概览',
            subtitle:
                '${s.connectedCount} 条协议在线 · ${devices.length} 台设备'
                ' · ${enabled.length} 个模块启用',
            pages: nav,
            actions: [
              // 右上角只留急停；「打开事件流」降到快速动作卡里。
              EstopButton(
                key: const ValueKey('dashboard-estop'),
                onTap: () => widget.actions.invoke('engine.emergency_stop'),
              ),
            ],
          ),
          children: [
            _ProtocolRow(channels: s.channels, slots: s.slots),
            const SizedBox(height: 12),
            IntrinsicHeight(
              // 两栏齐底：设备列表通常比右侧三颗按钮高。
              child: BentoLayout(
                stretch: true,
                left: [
                  BentoCard(
                    fit: true,
                    title: '连接设备',
                    accent: t.portPositive,
                    trailing: TinyLabel(
                      '${devices.length} 台',
                      color: t.textTertiary,
                      size: 9,
                    ),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
                      child: Column(
                        children: [
                          for (final slot in devices)
                            _DeviceRow(
                              slot: slot,
                              channel: s.channelLabel(slot.slotId),
                              onTap: () => widget.actions.selectSlot(slot.slotId),
                            ),
                          if (devices.isEmpty)
                            const EmptyNote('还没有设备，先去「连接」页配一台。'),
                        ],
                      ),
                    ),
                  ),
                ],
                right: [
                  BentoCard(
                    fit: true,
                    title: '数据流探测',
                    accent: t.portImage,
                    trailing: TinyLabel(
                      '${_lines.length} 条线路',
                      color: t.textTertiary,
                      size: 9,
                    ),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          for (final row in _lines) _FlowLineRow(row: row),
                          if (_lines.isEmpty)
                            const EmptyNote(
                              '画布上还没有「模块 ↔ 核心」之间连好的数据线。',
                            ),
                          if (_lines.isNotEmpty) const SizedBox(height: 2),
                          if (_lines.isNotEmpty)
                            Text(
                              '只统计事件流里已连线的两类线路：模块传出 → 核心写入、'
                              '核心传出 → 模块写入。',
                              style: TextStyle(
                                fontSize: 9.5,
                                height: 1.4,
                                color: t.textTertiary,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            IntrinsicHeight(
              child: BentoLayout(
                stretch: true,
                left: [
                  BentoCard(
                    fit: true,
                    title: '最近日志',
                    trailing: TinyLabel(
                      '最新 10 条',
                      color: t.textTertiary,
                      size: 9,
                    ),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(10, 6, 10, 6),
                      child: Column(
                        children: [
                          for (final (i, line)
                              in engine.logs.reversed.take(10).indexed)
                            _LogLine(key: ValueKey('dash-log-$i'), line: line),
                          if (engine.logs.isEmpty) const EmptyNote('暂时没有日志。'),
                        ],
                      ),
                    ),
                  ),
                ],
                right: [
                  BentoCard(
                    fit: true,
                    title: '已启用模块',
                    accent: t.accent,
                    trailing: TinyLabel(
                      '共 ${engine.modules.length} 个',
                      color: t.textTertiary,
                      size: 9,
                    ),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            crossAxisAlignment: CrossAxisAlignment.end,
                            children: [
                              Text(
                                '${enabled.length}',
                                style: TextStyle(
                                  fontSize: 28,
                                  height: 1,
                                  letterSpacing: -1,
                                  fontWeight: FontWeight.w600,
                                  color: t.textPrimary,
                                ),
                              ),
                              const SizedBox(width: 8),
                              Padding(
                                padding: const EdgeInsets.only(bottom: 3),
                                child: TinyLabel(
                                  '个模块正在运行',
                                  color: t.textTertiary,
                                  size: 9,
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 10),
                          for (final m in enabled)
                            Padding(
                              padding: const EdgeInsets.only(bottom: 5),
                              child: Row(
                                children: [
                                  Dot(color: t.accent, size: 3.5),
                                  const SizedBox(width: 7),
                                  Expanded(
                                    child: Text(
                                      '${m.name}  ${m.version}',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        fontSize: 10.5,
                                        height: 1.2,
                                        color: t.textSecondary,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          if (enabled.isEmpty) const EmptyNote('还没启用模块。'),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}

/// 三条协议并排：状态、连上来的设备数、这一条靠什么连（本地中继 / 远程 / 蓝牙）。
class _ProtocolRow extends StatelessWidget {
  const _ProtocolRow({required this.channels, required this.slots});

  final List<ChannelState> channels;
  final List<DeviceSlot> slots;

  @override
  Widget build(BuildContext context) {
    final order = const ['v4', 'v3', 'ble'];
    final cards = [
      for (final key in order)
        _ProtocolCard(
          channel: channels.firstWhere(
            (c) => c.key == key,
            orElse: () => ChannelState(key: key, label: EngineSnapshot.channelLabels[key]!),
          ),
          slots: slots,
        ),
    ];
    // 窗口窄的时候三张并排会挤成一团文字，改成竖着叠。
    return LayoutBuilder(
      builder: (context, box) {
        if (box.maxWidth < 860) {
          return Column(
            children: [
              for (final (i, card) in cards.indexed)
                Padding(
                  padding: EdgeInsets.only(bottom: i == cards.length - 1 ? 0 : 12),
                  child: card,
                ),
            ],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final (i, card) in cards.indexed) ...[
              if (i > 0) const SizedBox(width: 12),
              Expanded(child: card),
            ],
          ],
        );
      },
    );
  }
}

class _ProtocolCard extends StatelessWidget {
  const _ProtocolCard({required this.channel, required this.slots});

  final ChannelState channel;
  final List<DeviceSlot> slots;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final live = [
      for (final id in channel.slots)
        slots.where((s) => s.slotId == id).firstOrNull,
    ].whereType<DeviceSlot>().toList();
    final tone = channel.connected ? 1 : (channel.present ? 2 : 0);
    final state = channel.connected
        ? '已连接'
        : channel.present
        ? '等待客户端'
        : '未连接';
    return BentoCard(
      fit: true,
      title: channel.label,
      accent: channel.connected
          ? t.accent
          : channel.present
          ? const Color(0xFFF5C542)
          : t.textTertiary,
      trailing: StatusChip(text: state, tone: tone),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                TinyLabel('连接设备', color: t.textTertiary, size: 8.5),
                const SizedBox(height: 5),
                Text(
                  // 设备数按通道自己报的那份算：槽位表可能还没刷到这一条。
                  '${channel.slots.length}',
                  style: TextStyle(
                    fontSize: 22,
                    height: 1,
                    letterSpacing: -.6,
                    fontWeight: FontWeight.w600,
                    color: t.textPrimary,
                  ),
                ),
              ],
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    channel.summary,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 10.5,
                      height: 1.3,
                      color: t.textSecondary,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    live.isEmpty
                        ? (channel.statusText.isEmpty ? '—' : channel.statusText)
                        : [for (final s in live) s.label].join('、'),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9.5,
                      height: 1.3,
                      color: t.textTertiary,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 一台设备一行：名字与通道、强度 / 气压读数、输出状态与电量。
///
/// 这一页不画波形图：画图要按秒向引擎取采样再重绘一块 CustomPaint，三台设备就是
/// 三份常驻开销，而概览只是路过看一眼——数字够用了，波形留给控制页。
class _DeviceRow extends StatelessWidget {
  const _DeviceRow({
    required this.slot,
    required this.channel,
    required this.onTap,
  });

  final DeviceSlot slot;
  final String channel;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: HoverRow(
        onTap: onTap,
        builder: (context, hot) => Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(10, 9, 10, 10),
          decoration: BoxDecoration(
            color: hot ? Colors.white.withAlpha(8) : Colors.transparent,
            border: Border.all(color: t.hairline),
            borderRadius: BorderRadius.circular(AppTokens.radiusField),
          ),
          child: Row(
            children: [
              AvatarMark(
                name: slot.label,
                size: 28,
                seed: const [Color(0xFF2E7BE0), Color(0xFF48C8FF)],
              ),
              const SizedBox(width: 9),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      slot.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        height: 1.1,
                        fontWeight: FontWeight.w600,
                        color: t.textPrimary,
                      ),
                    ),
                    const SizedBox(height: 3),
                    TinyLabel(
                      '$channel · ${slot.type.isEmpty ? '设备' : slot.type}',
                      color: t.textTertiary,
                      size: 9,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              _Readouts(slot: slot),
              const SizedBox(width: 8),
              _WaveState(slot: slot),
              const SizedBox(width: 8),
              StatusChip(
                text: '${slot.battery ?? '--'}%',
                tone: (slot.battery ?? 0) > 20 ? 1 : 2,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 这一路现在在不在出力：设备推来的强度与通道状态就够判，不用另取一份波形。
class _WaveState extends StatelessWidget {
  const _WaveState({required this.slot});

  final DeviceSlot slot;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final String text;
    final Color color;
    if (!slot.isOutputDevice) {
      // 灵猫是传感器：边控触发了没有，就是它的「有没有在动」。
      final edge = slot.edgeState ?? 0;
      text = edge == 0 ? '边控 待机' : '边控 ${edge == 1 ? '刺激' : '冷静'}';
      color = edge == 0 ? t.textTertiary : t.portImage;
    } else {
      final firing = slot.of('A', slot.strength) > 0 ||
          slot.of('B', slot.strength) > 0;
      text = firing ? '波形 输出中' : '波形 静默';
      color = firing ? t.portPositive : t.textTertiary;
    }
    return TinyLabel(text, color: color, size: 9);
  }
}

/// 强度 / 气压读数：波形报的是窗口里的起伏，数字报的是「现在是多少」。
class _Readouts extends StatelessWidget {
  const _Readouts({required this.slot});

  final DeviceSlot slot;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final rows = slot.isOutputDevice
        ? [
            for (final ch in const ['A', 'B'])
              (
                ch,
                '${slot.of(ch, slot.strength)}/${slot.of(ch, slot.strengthLimit)}',
                ch == 'A' ? t.portPositive : t.portImage,
              ),
          ]
        : [(
            'kPa',
            (slot.pressure ?? 0).toStringAsFixed(2),
            t.accent,
          )];
    return Row(
      children: [
        for (final (label, value, color) in rows) ...[
          TinyLabel(label, color: t.textTertiary, size: 9),
          const SizedBox(width: 4),
          Text(
            value,
            style: TextStyle(
              fontFamily: 'Consolas',
              fontSize: 11,
              height: 1,
              color: color,
            ),
          ),
          const SizedBox(width: 9),
        ],
      ],
    );
  }
}

/// 一行日志：时刻 + 内容，单行显示、超长截断。
class _LogLine extends StatelessWidget {
  const _LogLine({super.key, required this.line});

  final LogEntry line;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final color = switch (line.level) {
      'error' => t.portNegative,
      'warn' => const Color(0xFFF5C542),
      'debug' => t.textTertiary,
      _ => t.textSecondary,
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 5),
      child: Row(
        children: [
          SizedBox(
            width: 58,
            child: Text(
              line.clock,
              style: TextStyle(
                fontFamily: 'Consolas',
                fontSize: 9.5,
                height: 1.3,
                color: t.textTertiary,
              ),
            ),
          ),
          const SizedBox(width: 6),
          Dot(color: color, size: 3.5),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              line.text,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontFamily: 'Consolas',
                fontSize: 10.5,
                height: 1.3,
                color: color,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 一条已连好的线路：`郊狼通道 A 强度[CORE] ← OSC 强度[OSC]`。
/// 核心恒在前项，箭头指的就是数据真正走的方向；公式摊开成多个来源时只报首项，
/// 其余以「等 N 个」补在后面。
class _FlowLineRow extends StatelessWidget {
  const _FlowLineRow({required this.row});

  final Map<String, Object?> row;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final toCore = '${row['kind'] ?? ''}' == 'to_core';
    final count = (row['count'] as num?)?.toInt() ?? 1;
    final arrow = toCore ? ' ← ' : ' → ';
    final color = toCore ? t.portPositive : t.portImage;
    TextStyle plain([bool strong = false]) => TextStyle(
      fontSize: 11,
      height: 1.5,
      fontWeight: strong ? FontWeight.w600 : FontWeight.w400,
      color: strong ? t.textPrimary : t.textSecondary,
    );
    final tag = TextStyle(
      fontFamily: 'Consolas',
      fontSize: 9.5,
      height: 1.5,
      color: t.textTertiary,
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Text.rich(
        TextSpan(
          children: [
            TextSpan(text: '${row['core'] ?? ''}', style: plain(true)),
            TextSpan(text: '[${row['coreShort'] ?? 'CORE'}]', style: tag),
            TextSpan(
              text: arrow,
              style: plain().copyWith(color: color),
            ),
            TextSpan(text: '${row['other'] ?? ''}', style: plain()),
            if ('${row['otherShort'] ?? ''}'.isNotEmpty)
              TextSpan(text: '[${row['otherShort']}]', style: tag),
            if (count > 1)
              TextSpan(text: ' 等 $count 个', style: plain()),
          ],
        ),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
    );
  }
}
