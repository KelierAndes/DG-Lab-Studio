import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart' hide Stack;

import '../app/tokens.dart';
import '../chrome/device_icons.dart';
import '../chrome/nav_rail.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../engine/engine_models.dart';
import '../engine/engine_state.dart';
import 'actions.dart';
import 'app_pages.dart';

/// 连接：三条通道平铺，各自连接、各自断开、各自列自己的设备。
///
/// 本地中继不是第四种通道——它是 v4 / v3 通道自己的选项（地址与端口在设置页），
/// 旧界面就是这样：卡片里只给「连这条通道」和这条通道的配对二维码 / 已接入设备。
class ConnectPage extends StatefulWidget {
  const ConnectPage({
    super.key,
    required this.nav,
    required this.engine,
    required this.actions,
  });

  final AppNav nav;
  final StudioEngine engine;
  final StudioActions actions;

  @override
  State<ConnectPage> createState() => _ConnectPageState();
}

class _ConnectPageState extends State<ConnectPage> {
  /// 每条通道当前选中的设备槽位；蓝牙卡的扫描 / 保存列表各记一条。
  final Map<String, String?> _picked = {};
  final Map<String, _Qr> _qr = {};
  final Set<String> _qrBusy = {};
  bool _scanning = false;

  StudioEngine get engine => widget.engine;

  void _pick(String key, String slotId) {
    setState(() => _picked[key] = slotId);
    widget.nav.selectSlot(slotId);
  }

  Future<void> _loadQr(String key) async {
    final result = await widget.actions.invoke('engine.qr', {'channel': key});
    if (!mounted || result is! Map) return;
    final png = '${result['png'] ?? ''}';
    setState(
      () => _qr[key] = _Qr(
        text: '${result['text'] ?? ''}',
        image: png.isEmpty ? null : MemoryImage(base64Decode(png)),
        channel: '${result['channel'] ?? key}',
      ),
    );
  }

  /// 通道连上才取二维码，右侧那一栏常驻显示它——旧界面不用人去翻折叠块。
  /// 断开（含自动重连中）时把缓存的旧图丢掉：留着就会看到断开的通道还在亮二维码。
  void _followQr(EngineSnapshot s) {
    for (final key in const ['v4', 'v3']) {
      final channel = s.channel(key);
      if (!channel.connected) {
        // 只丢缓存，不 setState：这里在 build 里跑，本帧后面读的就是空值。
        _qr.remove(key);
        continue;
      }
      if (channel.qrText.isEmpty) continue;
      final cached = _qr[key];
      // 换客户端 ID 时链接会变，旧图必须跟着重取，不然扫出来还是上一条连接。
      if (cached != null &&
          cached.image != null &&
          cached.text == channel.qrText) {
        continue;
      }
      if (_qrBusy.contains(key)) continue;
      _qrBusy.add(key);
      unawaited(_loadQr(key).whenComplete(() => _qrBusy.remove(key)));
    }
  }

  Future<void> _scan() async {
    if (_scanning) return;
    setState(() => _scanning = true);
    await engine.scanBle();
    if (mounted) setState(() => _scanning = false);
  }

  /// 设置页里那条「本地中继」偏好与端口：连接按钮按它选方法，这里只显状态。
  Map<String, Object?> get _relay {
    final r = engine.config['relay'];
    return r is Map ? Map<String, Object?>.from(r) : const {};
  }

  int _port(Object? v, int fallback) => (v as num?)?.toInt() ?? fallback;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: Listenable.merge([widget.nav, engine]),
      builder: (context, _) {
        final s = engine.snapshot;
        _followQr(s);
        return PageBody(
          pages: widget.nav,
          header: PageHeader(
            title: '连接',
            subtitle: '按通道管理：Socket V4 / V3 中继与蓝牙直连，三条通道可以同时在线',
            pages: widget.nav,
            search: false,
            actions: [
              // 右上角只留急停这一颗，其余动作挪到正文第一排。
              EstopButton(
                key: const ValueKey('connect-estop'),
                onTap: () => widget.actions.invoke('engine.emergency_stop'),
              ),
            ],
          ),
          children: [
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Row(
                children: [
                  TinyLabel(
                    '三条通道互不影响：连着的那条随时可以断开',
                    color: t.textTertiary,
                    size: 9.5,
                  ),
                  const Spacer(),
                  OptionPill(
                    key: const ValueKey('connect-disconnect-all'),
                    label: '断开当前连接',
                    active: false,
                    dense: true,
                    onTap: s.connectedCount == 0
                        ? () {}
                        : () => widget.actions.invoke('engine.disconnect'),
                  ),
                  const SizedBox(width: 7),
                  OptionPill(
                    key: const ValueKey('connect-rescan'),
                    label: _scanning ? '扫描中…' : '重新扫描蓝牙',
                    active: true,
                    dense: true,
                    onTap: _scan,
                  ),
                ],
              ),
            ),
            _RelayCard(
              channel: s.channel('v4'),
              title: 'Socket V4 连接',
              address: '${engine.config['v4_url'] ?? ''}',
              icon: Icons.link_rounded,
              connectMethod: 'engine.connect_v4',
              localMethod: 'engine.connect_v4_local',
              preferLocal: _relay['v4_local'] == true,
              relayPort: _port(_relay['v4_port'], 9998),
              picked: _picked['v4'],
              slots: _slotsFor(s, s.channel('v4')),
              qr: _qr['v4'],
              accent: t.accent,
              onPick: (sid) => _pick('v4', sid),
              actions: widget.actions,
              engine: engine,
            ),
            const SizedBox(height: 12),
            _RelayCard(
              channel: s.channel('v3'),
              title: 'Socket V3 连接',
              address: '${engine.config['v3_url'] ?? ''}',
              icon: Icons.wifi_tethering_rounded,
              connectMethod: 'engine.connect_v3',
              localMethod: 'engine.connect_v3_local',
              preferLocal: _relay['v3_local'] == true,
              relayPort: _port(_relay['v3_port'], 9999),
              picked: _picked['v3'],
              slots: _slotsFor(s, s.channel('v3')),
              qr: _qr['v3'],
              accent: t.portImage,
              onPick: (sid) => _pick('v3', sid),
              actions: widget.actions,
              engine: engine,
            ),
            const SizedBox(height: 12),
            _BleCard(
              channel: s.channel('ble'),
              picked: _picked['ble'],
              scanPick: _picked['ble-scan'],
              savedPick: _picked['ble-saved'],
              slots: _slotsFor(s, s.channel('ble')),
              scanning: _scanning,
              accent: t.portModel,
              onPick: (sid) => _pick('ble', sid),
              onPickScan: (address) =>
                  setState(() => _picked['ble-scan'] = address),
              onPickSaved: (address) =>
                  setState(() => _picked['ble-saved'] = address),
              onScan: _scan,
              actions: widget.actions,
              engine: engine,
            ),
          ],
        );
      },
    );
  }

  /// 一条通道只列自己接入的设备：合并视图里的槽位按通道回查。
  List<DeviceSlot> _slotsFor(EngineSnapshot s, ChannelState c) => [
    for (final slot in s.slots)
      if (c.slots.contains(slot.slotId)) slot,
  ];
}

class _Qr {
  _Qr({required this.text, required this.image, required this.channel});

  final String text;

  /// 解码只做一次、图片对象留着复用：`MemoryImage` 按字节实例比身份，
  /// 每帧重新 `base64Decode` 会让 Image 以为换了图，先清空再解码——
  /// 二维码那一闪一闪就是这么来的（引擎每推一条日志这页就重建一次）。
  final MemoryImage? image;
  final String channel;
}

class _RelayCard extends StatelessWidget {
  const _RelayCard({
    required this.channel,
    required this.title,
    required this.address,
    required this.icon,
    required this.connectMethod,
    required this.localMethod,
    required this.preferLocal,
    required this.relayPort,
    required this.picked,
    required this.slots,
    required this.qr,
    required this.accent,
    required this.onPick,
    required this.actions,
    required this.engine,
  });

  final ChannelState channel;
  final String title;
  final String address;
  final IconData icon;
  final String connectMethod;
  final String localMethod;

  /// 设置页里这条通道的「本地中继」偏好：连接按钮按它选方法。
  final bool preferLocal;
  final int relayPort;
  final String? picked;
  final List<DeviceSlot> slots;
  final _Qr? qr;
  final Color accent;
  final void Function(String slotId) onPick;
  final StudioActions actions;
  final StudioEngine engine;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final local = channel.localRelay;
    return BentoCard(
      key: ValueKey('card-${channel.key}'),
      fit: true,
      title: title,
      accent: accent,
      trailing: StatusChip(
        text: channel.connected ? (channel.paired ? '已连接' : '等待扫码配对') : '未启用',
        tone: channel.connected ? (channel.paired ? 1 : 2) : 0,
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 2),
        // 二维码 / 连接信息常驻在卡片右侧一栏，不再做成要手动展开的折叠块；
        // 通道停用（没有客户端）时这一栏清空，只留一行说明。
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(icon, size: 15, color: t.textTertiary),
                      const SizedBox(width: 7),
                      Expanded(
                        child: Text(
                          local
                              ? '本地中继 ws://局域网:${channel.relayPort ?? '--'}（手机与电脑需同一网络）'
                              : '远程中继 $address',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 11,
                            height: 1.2,
                            color: t.textSecondary,
                          ),
                        ),
                      ),
                      TinyLabel(
                        channel.primary ? '主用' : '',
                        color: t.accent,
                        size: 9,
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  GroupPanel(
                    label: '连接方式',
                    // 走不走本地中继由「设置 → 连接」里那条开关决定，这里只跟着它选方法，
                    // 不再放第二颗按钮：两处开关、两种真相。
                    hint: preferLocal
                        ? '设置页已选择本地中继 · 端口 $relayPort（手机与电脑需同一网络）'
                        : '设置页可选择「本地中继」改走局域网；服务器地址也在设置页',
                    child: Row(
                      children: [
                        Expanded(
                          flex: 2,
                          child: GhostButton(
                            key: ValueKey('connect-${channel.key}'),
                            label: preferLocal ? '连接（本地中继）' : '连接并生成配对二维码',
                            dense: true,
                            active: true,
                            onTap: () => actions.invoke(
                              preferLocal ? localMethod : connectMethod,
                              {'channel': channel.key},
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: GhostButton(
                            key: ValueKey('disconnect-${channel.key}'),
                            label: '断开连接',
                            dense: true,
                            onTap: channel.present
                                ? () => actions.invoke(
                                    'engine.disconnect_channel',
                                    {'channel': channel.key},
                                  )
                                : () {},
                          ),
                        ),
                      ],
                    ),
                  ),
                  GroupPanel(
                    label: '已接入设备',
                    hint: '点一行选中它，控制页与断开都跟着这一台',
                    child: _DeviceRows(
                      picked: picked,
                      slots: slots,
                      onPick: onPick,
                      empty: '该通道暂无已接入设备',
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              width: 176,
              child: _QrPane(channel: channel, qr: qr),
            ),
          ],
        ),
      ),
    );
  }
}

/// 卡片右侧常驻的一栏：启用时给二维码 + 配对链接 + 连接地址，停用时留空。
class _QrPane extends StatelessWidget {
  const _QrPane({required this.channel, required this.qr});

  final ChannelState channel;
  final _Qr? qr;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    // 只看「这条通道现在真的连着」：通道对象还在但链路断了（自动重连中）也不能再亮二维码。
    if (!channel.connected) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TinyLabel('配对二维码 / 连接信息', color: t.textTertiary, size: 9),
          const SizedBox(height: 6),
          Text(
            '未启用 · 连接后在这里显示二维码与连接地址。',
            style: TextStyle(fontSize: 10, height: 1.5, color: t.textTertiary),
          ),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        TinyLabel('配对二维码 / 连接信息', color: t.textTertiary, size: 9),
        const SizedBox(height: 6),
        _QrBlock(qr: qr, channel: channel),
      ],
    );
  }
}

class _QrBlock extends StatelessWidget {
  const _QrBlock({required this.qr, required this.channel});

  final _Qr? qr;
  final ChannelState channel;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final text = qr?.text ?? channel.qrText;
    final image = qr?.image;
    if (text.isEmpty) {
      return Text(
        '还没有配对链接：连上中继后才会生成。',
        style: TextStyle(fontSize: 10.5, height: 1.4, color: t.textTertiary),
      );
    }
    // 右侧栏只有 176 宽，二维码与信息改成上下排：图在上，链接与 ID 在下。
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (image != null)
          Container(
            width: 150,
            height: 150,
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(AppTokens.radiusField),
              border: Border.all(color: t.hairline),
            ),
            // gaplessPlayback：真的换图（重连换了客户端 ID）时保留上一帧过渡，
            // 不先清空成白底。
            child: Image(
              key: ValueKey('qr-${channel.key}-$text'),
              image: image,
              fit: BoxFit.contain,
              gaplessPlayback: true,
            ),
          )
        else
          SizedBox(
            width: 150,
            height: 150,
            child: Center(
              child: TinyLabel('二维码图片还没取到\n可先复制下面的配对链接', color: t.textTertiary),
            ),
          ),
        const SizedBox(height: 8),
        TinyLabel('连接地址', color: t.textTertiary, size: 9),
        const SizedBox(height: 3),
        SelectableText(
          text,
          style: TextStyle(
            fontFamily: 'Consolas',
            fontSize: 9.5,
            height: 1.4,
            color: t.textSecondary,
          ),
        ),
        const SizedBox(height: 8),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TinyLabel('客户端 ID', color: t.textTertiary, size: 9),
                  const SizedBox(height: 3),
                  _Mono(channel.clientId.isEmpty ? '--' : channel.clientId),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TinyLabel('对端', color: t.textTertiary, size: 9),
                  const SizedBox(height: 3),
                  _Mono(channel.targetId.isEmpty ? '--' : channel.targetId),
                ],
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _BleCard extends StatelessWidget {
  const _BleCard({
    required this.channel,
    required this.picked,
    required this.scanPick,
    required this.savedPick,
    required this.slots,
    required this.scanning,
    required this.accent,
    required this.onPick,
    required this.onPickScan,
    required this.onPickSaved,
    required this.onScan,
    required this.actions,
    required this.engine,
  });

  final ChannelState channel;
  final String? picked;
  final String? scanPick;
  final String? savedPick;
  final List<DeviceSlot> slots;
  final bool scanning;
  final Color accent;
  final void Function(String slotId) onPick;
  final void Function(String address) onPickScan;
  final void Function(String address) onPickSaved;
  final VoidCallback onScan;
  final StudioActions actions;
  final StudioEngine engine;

  List<Map<String, Object?>> get _saved {
    final raw = engine.config['saved_devices'];
    if (raw is! List) return const [];
    return [
      for (final d in raw)
        if (d is Map) Map<String, Object?>.from(d),
    ];
  }

  bool get _autoReconnect => engine.config['auto_reconnect'] == true;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final results = engine.bleResults;
    final saved = _saved;
    return BentoCard(
      key: const ValueKey('card-ble'),
      fit: true,
      title: '蓝牙直连',
      accent: accent,
      trailing: StatusChip(
        text: channel.connected ? '已连接 ${channel.slots.length} 台' : '未连接',
        tone: channel.connected ? 1 : 0,
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            GroupPanel(
              label: '扫描附近设备',
              hint: scanning
                  ? '正在扫描附近的设备（约 6 秒）…'
                  : (results.isEmpty
                        ? '尚未扫描：点右侧「扫描设备」开始（约 6 秒）'
                        : '已发现 ${results.length} 台；点一行选中后连接'),
              trailing: GhostButton(
                key: const ValueKey('ble-scan'),
                label: scanning ? '扫描中…' : '扫描设备',
                icon: Icons.radar_rounded,
                dense: true,
                onTap: onScan,
              ),
              child: results.isEmpty
                  ? const EmptyNote('还没有扫描结果。')
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        for (final d in results)
                          TileRow(
                            key: ValueKey('ble-scan-row-${d['address']}'),
                            selected: scanPick == '${d['address']}',
                            onTap: () => onPickScan('${d['address']}'),
                            leading: DeviceTile(
                              family: familyOfKind('${d['kind']}'),
                              size: 26,
                              active: scanPick == '${d['address']}',
                            ),
                            title: '${d['name']}',
                            subtitle:
                                '${ConnectLabels.kind('${d['kind']}')} · ${d['address']}'
                                '${d['rssi'] == null ? '' : ' · RSSI ${d['rssi']}'}',
                            trailing: StatusChip(
                              text: scanPick == '${d['address']}'
                                  ? '已选中'
                                  : '可连接',
                              tone: scanPick == '${d['address']}' ? 1 : 0,
                            ),
                          ),
                        const SizedBox(height: 6),
                        GhostButton(
                          key: const ValueKey('ble-connect-scan'),
                          label: '连接选中设备',
                          dense: true,
                          active: true,
                          onTap: scanPick == null
                              ? () {}
                              : () => actions.invoke('engine.ble_connect', {
                                  'address': scanPick,
                                  'kind': _kindOf(scanPick),
                                }, const Duration(seconds: 40)),
                        ),
                      ],
                    ),
            ),
            GroupPanel(
              label: '已保存设备',
              hint: '连接成功的设备会自动记在这里，可按 MAC 回连',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final d in saved)
                    TileRow(
                      key: ValueKey('ble-saved-row-${d['address']}'),
                      selected: savedPick == '${d['address']}',
                      onTap: () => onPickSaved('${d['address']}'),
                      leading: DeviceTile(
                        family: familyOfKind('${d['kind']}'),
                        size: 26,
                        active: savedPick == '${d['address']}',
                      ),
                      title: '${d['name'] ?? d['address']}',
                      subtitle:
                          '${ConnectLabels.kind('${d['kind']}')} · ${d['address']}',
                      trailing: StatusChip(
                        text: savedPick == '${d['address']}' ? '已选中' : '已保存',
                        tone: savedPick == '${d['address']}' ? 1 : 3,
                      ),
                    ),
                  if (saved.isEmpty) const EmptyNote('暂无保存记录：连接成功的设备会自动记录。'),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      Expanded(
                        child: GhostButton(
                          key: const ValueKey('ble-reconnect-saved'),
                          label: '连接选中设备',
                          dense: true,
                          onTap: savedPick == null
                              ? () {}
                              : () => actions.invoke(
                                  'engine.ble_reconnect_saved',
                                  {'address': savedPick},
                                  const Duration(seconds: 40),
                                ),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: GhostButton(
                          key: const ValueKey('ble-forget'),
                          label: '删除记录',
                          dense: true,
                          onTap: savedPick == null
                              ? () {}
                              : () => actions.invoke('engine.forget_device', {
                                  'address': savedPick,
                                }),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            GroupPanel(
              label: '通道设置',
              hint: '断开后自动按保存的 MAC 重试（约每 20 秒一次）',
              child: Row(
                children: [
                  TinyLabel('自动重连', color: t.textTertiary),
                  const SizedBox(width: 8),
                  SwitchPill(
                    key: const ValueKey('ble-auto-reconnect'),
                    on: _autoReconnect,
                    onTap: () => actions.invoke('engine.config_set', {
                      'path': 'auto_reconnect',
                      'value': !_autoReconnect,
                    }),
                  ),
                ],
              ),
            ),
            GroupPanel(
              label: '本通道已接入设备',
              hint: '点一行选中它；断开只断这一台，或整条蓝牙通道一起断',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _DeviceRows(
                    picked: picked,
                    slots: slots,
                    onPick: onPick,
                    empty: '该通道暂无已接入设备',
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      Expanded(
                        child: GhostButton(
                          key: const ValueKey('ble-disconnect-picked'),
                          label: '断开选中',
                          dense: true,
                          onTap: picked == null
                              ? () {}
                              : () => actions.invoke('engine.ble_disconnect', {
                                  'slot_id': picked,
                                }),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: GhostButton(
                          key: const ValueKey('ble-disconnect-all'),
                          label: '断开蓝牙通道',
                          dense: true,
                          onTap: channel.present
                              ? () => actions.invoke(
                                  'engine.disconnect_channel',
                                  {'channel': 'ble'},
                                )
                              : () {},
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _kindOf(String? address) {
    for (final d in _saved) {
      if ('${d['address']}' == address) return '${d['kind'] ?? 'coyote_v3'}';
    }
    for (final d in engine.bleResults) {
      if ('${d['address']}' == address) return '${d['kind'] ?? 'coyote_v3'}';
    }
    return 'coyote_v3';
  }
}

class _DeviceRows extends StatelessWidget {
  const _DeviceRows({
    required this.picked,
    required this.slots,
    required this.onPick,
    required this.empty,
  });

  final String? picked;
  final List<DeviceSlot> slots;
  final void Function(String slotId) onPick;
  final String empty;

  @override
  Widget build(BuildContext context) {
    if (slots.isEmpty) return EmptyNote(empty);
    return Column(
      children: [
        for (final slot in slots)
          TileRow(
            key: ValueKey('channel-device-${slot.slotId}'),
            selected: picked == slot.slotId,
            onTap: () => onPick(slot.slotId),
            leading: DeviceTile(
              family: familyOfDeviceType(slot.type),
              size: 26,
              active: picked == slot.slotId,
            ),
            title: slot.label,
            subtitle:
                '${ConnectLabels.family(familyOfDeviceType(slot.type))} · ${slot.type}'
                ' · ${slot.battery == null ? '--' : '${slot.battery}%'}',
            trailing: StatusChip(
              text: picked == slot.slotId ? '已选中' : '在线',
              tone: picked == slot.slotId ? 1 : 0,
            ),
          ),
      ],
    );
  }
}

class _Mono extends StatelessWidget {
  const _Mono(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Text(
      text,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: TextStyle(
        fontFamily: 'Consolas',
        fontSize: 11,
        height: 1.2,
        color: t.textPrimary,
      ),
    );
  }
}

/// 设备家族与型号 kind 的中文名，与旧界面 live.FAMILY_LABELS 同一套读法。
class ConnectLabels {
  static String family(String f) => switch (f) {
    'OVC' => '负鼠 (振动)',
    'BMTR' => '灵猫 (气压)',
    _ => '郊狼 (电刺激)',
  };

  static String kind(String k) => switch (k) {
    'ovc' => '负鼠 OVC',
    'bmtr' => '灵猫 BMTR',
    'coyote_v3' => '郊狼 V3',
    'coyote_v2' => '郊狼 V2',
    _ => k,
  };
}

String familyOfDeviceType(String type) {
  final t = type.toUpperCase();
  if (t.startsWith('OVC')) return 'OVC';
  if (t.startsWith('BMTR')) return 'BMTR';
  return 'COYOTE';
}

String familyOfKind(String kind) => switch (kind) {
  'ovc' => 'OVC',
  'bmtr' => 'BMTR',
  _ => 'COYOTE',
};
