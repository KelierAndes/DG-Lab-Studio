import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart' hide Stack;
import 'package:window_manager/window_manager.dart';

import '../app/field_metrics.dart';
import '../app/studio_prefs.dart';
import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../engine/engine_state.dart';
import 'actions.dart';
import 'app_pages.dart';
import 'update_prompt.dart';

/// 设置：外观属于外壳，连接、市场、日志与配置文件属于引擎。
class SettingsPage extends StatelessWidget {
  const SettingsPage({
    super.key,
    required this.nav,
    required this.engine,
    required this.prefs,
    required this.actions,
  });

  final AppNav nav;
  final StudioEngine engine;
  final StudioPrefs prefs;
  final StudioActions actions;

  Map<String, Object?> _map(String key) {
    final raw = engine.config[key];
    return raw is Map ? Map<String, Object?>.from(raw) : const {};
  }

  bool _on(String key) => _map('settings')[key] == true;
  int _int(String key) {
    final v = _map('settings')[key];
    return v is num ? v.round() : 0;
  }

  String _str(String key) => '${_map('settings')[key] ?? ''}';

  void _set(String key, Object? value) =>
      engine.invoke('settings.set', {'key': key, 'value': value});

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final paths = _map('paths');
    final version = _map('version');
    return AnimatedBuilder(
      animation: Listenable.merge([nav, engine, prefs]),
      builder: (context, _) => PageBody(
        pages: nav,
        header: PageHeader(
          title: '设置',
          subtitle: '改动即时写回 config.json',
          pages: nav,
          search: false,
          actions: [
            // 急停在每页右上角都是同一颗红键，设置页也不例外。
            EstopButton(
              key: const ValueKey('settings-estop'),
              onTap: () => engine.invoke('engine.emergency_stop'),
            ),
          ],
        ),
        children: [
          IntrinsicHeight(
            // 同一排两张卡齐底：短的那张补齐外框，不留参差的空隙。
            child: BentoLayout(
              stretch: true,
              left: [
                BentoCard(
                  fit: true,
                  title: '连接',
                  accent: t.accent,
                  child: _Rows(
                    children: [
                      _SwitchRow(
                        label: '自动重连',
                        desc: '蓝牙连接意外断开时每 5 秒重试',
                        on: _on('connection.auto_reconnect'),
                        onTap: () => _set(
                          'connection.auto_reconnect',
                          !_on('connection.auto_reconnect'),
                        ),
                      ),
                      _TextRow(
                        label: 'Socket V4 中继地址',
                        desc: '留空使用默认官方中继',
                        value: _str('connection.v4_url'),
                        onCommit: (v) => _set('connection.v4_url', v),
                      ),
                      _SwitchRow(
                        label: 'V4 本地中继',
                        desc: '本机作为局域网中继，App 扫码直连',
                        on: _on('connection.v4_local'),
                        onTap: () => _set(
                          'connection.v4_local',
                          !_on('connection.v4_local'),
                        ),
                      ),
                      _IntRow(
                        label: 'V4 本地端口',
                        value: _int('connection.v4_port'),
                        onStep: (v) => _set('connection.v4_port', v),
                      ),
                      _TextRow(
                        label: 'Socket V3 中继地址',
                        desc: '留空使用默认官方中继',
                        value: _str('connection.v3_url'),
                        onCommit: (v) => _set('connection.v3_url', v),
                      ),
                      _SwitchRow(
                        label: 'V3 本地中继',
                        desc: '本机作为局域网中继服务器',
                        on: _on('connection.v3_local'),
                        onTap: () => _set(
                          'connection.v3_local',
                          !_on('connection.v3_local'),
                        ),
                      ),
                      _IntRow(
                        label: 'V3 本地端口',
                        value: _int('connection.v3_port'),
                        onStep: (v) => _set('connection.v3_port', v),
                      ),
                    ],
                  ),
                ),
              ],
              right: [
                BentoCard(
                  fit: true,
                  title: '外观与日志',
                  child: _Rows(
                    children: [
                      _SwitchRow(
                        key: const ValueKey('settings-dark'),
                        label: '深色主题',
                        desc: '浅色为亮色调色板',
                        on: !prefs.lightTheme,
                        onTap: prefs.toggleTheme,
                      ),
                      _SwitchRow(
                        key: const ValueKey('settings-grid'),
                        label: '画布网格',
                        desc: '事件流页的底纹',
                        on: prefs.gridOn,
                        onTap: prefs.toggleGrid,
                      ),
                      _SwitchRow(
                        key: const ValueKey('settings-drawer'),
                        label: '左栏抽屉',
                        desc: '设备、模块与卡片面板',
                        on: prefs.drawerOpen,
                        onTap: prefs.toggleDrawer,
                      ),
                      _SwitchRow(
                        label: '写日志文件',
                        desc: '记录到应用目录 dgstudio.log',
                        on: _on('log.file'),
                        onTap: () => _set('log.file', !_on('log.file')),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          BentoCard(
            fit: true,
            title: '模块市场',
            accent: t.portImage,
            trailing: TinyLabel(
              '在线模块的下载源、加速与代理',
              color: t.textTertiary,
              size: 9,
            ),
            child: _Rows(
              children: [
                _TextRow(
                  label: '市场仓库',
                  desc: '留空 = 官方 KelierAndes/dgstudio-modules-market；可填 owner/name 或 owner/name@branch',
                  value: _str('market.repo'),
                  onCommit: (v) => _set('market.repo', v),
                ),
                _TextRow(
                  label: 'GitHub 加速前缀',
                  desc: '直连失败时的常用加速源；留空直连',
                  value: _str('market.mirror'),
                  onCommit: (v) => _set('market.mirror', v),
                ),
                _TextRow(
                  label: '网络代理',
                  desc: '常用端口：Clash 7890 / Clash Verge 7897 / v2rayN 10809；留空跟随系统代理',
                  value: _str('market.proxy'),
                  onCommit: (v) => _set('market.proxy', v),
                ),
                _SwitchRow(
                  label: '忽略系统代理',
                  desc: '不使用系统/环境代理直连 GitHub（设置了网络代理时无效）',
                  on: _on('market.no_proxy'),
                  onTap: () => _set('market.no_proxy', !_on('market.no_proxy')),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          BentoCard(
            fit: true,
            title: '软件更新',
            accent: t.portPositive,
            trailing: TinyLabel(
              '只查 GitHub 最新 release，不会偷偷装',
              color: t.textTertiary,
              size: 9,
            ),
            child: _Rows(
              children: [
                _UpdateRow(engine: engine),
                _SwitchRow(
                  key: const ValueKey('settings-update-auto'),
                  label: '启动时检查更新',
                  desc: '连上引擎后查一次，有新版只提示不安装',
                  on: _on('update.auto'),
                  onTap: () => _set('update.auto', !_on('update.auto')),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          IntrinsicHeight(
            // 配置文件与关于同排：两栏齐底。
            child: BentoLayout(
              stretch: true,
              left: [
                BentoCard(
                  fit: true,
                  title: '配置文件',
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _PathRow(
                          label: '主配置文件',
                          value: '${paths['config'] ?? ''}',
                        ),
                        const SizedBox(height: 8),
                        _PathRow(
                          label: '模块配置目录',
                          value: '${paths['modules'] ?? ''}',
                        ),
                        const SizedBox(height: 10),
                        Row(
                          children: [
                            Expanded(
                              child: GhostButton(
                                key: const ValueKey('settings-save'),
                                label: '保存到文件',
                                icon: Icons.save_outlined,
                                dense: true,
                                onTap: () async {
                                  await engine.invoke('config.save_all');
                                  actions.toast('配置已全部保存到文件');
                                },
                              ),
                            ),
                            const SizedBox(width: 6),
                            Expanded(
                              child: GhostButton(
                                key: const ValueKey('settings-load'),
                                label: '从文件载入…',
                                icon: Icons.folder_open_rounded,
                                dense: true,
                                onTap: () =>
                                    _loadFromFile('${paths['modules'] ?? ''}'),
                              ),
                            ),
                            const SizedBox(width: 6),
                            Expanded(
                              child: GhostButton(
                                key: const ValueKey('settings-export'),
                                label: '导出全部…',
                                icon: Icons.ios_share_rounded,
                                dense: true,
                                onTap: _exportAll,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '载入会写盘并重启运行中的模块立即生效',
                          style: TextStyle(
                            fontSize: 9.5,
                            height: 1.3,
                            color: t.textTertiary,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              right: [
                BentoCard(
                  fit: true,
                  title: '关于',
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Text(
                              'DGStudio',
                              style: TextStyle(
                                fontSize: 14,
                                height: 1,
                                fontWeight: FontWeight.w600,
                                letterSpacing: -.2,
                                color: t.textPrimary,
                              ),
                            ),
                            const SizedBox(width: 8),
                            StatusChip(
                              text: 'v${version['app'] ?? '0.6.0'}',
                              tone: 1,
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),
                        _InfoRow(
                          label: '界面',
                          value: '${version['shell'] ?? 'Flutter Windows'}',
                        ),
                        const SizedBox(height: 6),
                        _InfoRow(
                          label: '引擎',
                          value: '${version['engine'] ?? 'Python sidecar'}',
                        ),
                        const SizedBox(height: 6),
                        _InfoRow(
                          label: '桥协议',
                          value: 'v${engine.config['bridge_version'] ?? 1}',
                        ),
                        const SizedBox(height: 10),
                        Text(
                          '配置写入 config.json，模块设置写入 config/ 目录',
                          style: TextStyle(
                            fontSize: 9.5,
                            height: 1.3,
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
        ],
      ),
    );
  }

  Future<void> _loadFromFile(String initialDir) async {
    PlatformFile? picked;
    try {
      picked = await FilePicker.pickFile(
        dialogTitle: '选择要载入的配置文件',
        initialDirectory: initialDir,
      );
    } on Object {
      actions.toast('这台机器上打不开系统文件对话框');
      return;
    }
    final path = picked?.path;
    if (path == null) return;
    final r = await engine.invoke('config.load_from', {
      'path': path,
    }, const Duration(minutes: 2));
    if (r is Map) {
      actions.toast('已载入 ${r['applied']} 个文件，重启模块 ${r['restarted']} 个');
    }
    await engine.refresh();
  }

  Future<void> _exportAll() async {
    final bundle = await engine.invoke(
      'config.bundle',
      const {},
      const Duration(seconds: 60),
    );
    if (bundle is! String) {
      actions.toast('导出失败：没拿到配置内容');
      return;
    }
    Uri? uri;
    try {
      uri = await FilePicker.saveFile(
        dialogTitle: '导出全部配置',
        fileName: 'dgstudio-config.json',
        bytes: utf8.encode(bundle),
        mimeType: 'application/json',
      );
    } on Object {
      uri = null; // 对话框在某些环境下不可用，下面有落点，不能让按钮点了没反应。
    }
    if (uri == null) {
      final r = await engine.invoke('config.export_default');
      if (r is Map) actions.toast('已导出到 ${_shorten('${r['path']}')}');
      return;
    }
    actions.toast('已导出到 ${_shorten(uri.toString())}');
  }

  static String _shorten(String path) =>
      path.length <= 44 ? path : '…${path.substring(path.length - 42)}';
}

/// 下载进度 / 校验状态：一条细进度条 + 「12.3 / 53.0 MB · 23% · 直连」。
class _UpdateProgress extends StatelessWidget {
  const _UpdateProgress({required this.engine});

  final StudioEngine engine;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final stage = engine.updateStage;
    final source = engine.updateSource;
    final fraction = engine.updateFraction;
    final received = engine.updateReceived;
    final total = engine.updateTotal;
    final (text, color) = switch (stage) {
      'verify' => ('正在校验 SHA-256 与发布签名…', t.textSecondary),
      'done' => (
        '已下载并校验通过（SHA-256 + 签名）'
            '${source.isEmpty ? '' : ' · $source'}',
        t.portPositive
      ),
      'error' => (engine.updateError ?? '更新失败', t.portNegative),
      _ => (
        total > 0
            ? '${formatBytes(received)} / ${formatBytes(total)}'
                ' · ${(fraction == null ? 0 : fraction * 100).round()}%'
                '${source.isEmpty ? '' : ' · $source'}'
            : '正在下载…${source.isEmpty ? '' : ' · $source'}',
        t.textSecondary
      ),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (stage == 'download')
          LayoutBuilder(
            builder: (context, box) => Container(
              height: 8,
              decoration: BoxDecoration(
                color: t.wellDeep,
                borderRadius: BorderRadius.circular(4),
              ),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Container(
                  width: box.maxWidth * (fraction ?? 0),
                  decoration: BoxDecoration(
                    color: t.portPositive,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ),
            ),
          ),
        if (stage == 'download') const SizedBox(height: 5),
        Text(
          text,
          key: const ValueKey('update-progress-text'),
          style: TextStyle(fontSize: 9.5, height: 1.3, color: color),
        ),
      ],
    );
  }
}

/// 软件更新：显示当前 / 最新版本，检查、下载并交给外部脚本替换后重启。
class _UpdateRow extends StatelessWidget {
  const _UpdateRow({required this.engine});

  final StudioEngine engine;

  Future<void> _run() async {
    // 侧车的更新脚本在等这个进程退出，关窗即交接。
    if (await engine.runUpdate()) await windowManager.close();
  }

  String get _status {
    if (engine.updateChecking) return '检查中…';
    if (engine.updateError != null) return '检查失败';
    final info = engine.updateInfo;
    if (info == null) return '未检查';
    return engine.updateAvailable ? '可更新 v${info['latest']}' : '已是最新';
  }

  int get _tone {
    if (engine.updateError != null) return 2;
    if (engine.updateApplying || engine.updateDownloading) return 3;
    if (engine.updateInfo == null) return 0;
    return engine.updateAvailable ? 1 : 3;
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final info = engine.updateInfo;
    final notes = '${info?['notes'] ?? ''}'.trim();
    final busy = engine.updateChecking ||
        engine.updateDownloading ||
        engine.updateApplying;
    return Padding(
      padding: const EdgeInsets.fromLTRB(0, 4, 0, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              StatusChip(text: _status, tone: _tone),
              const SizedBox(width: 8),
              Text(
                '当前 v${engine.config['version'] is Map ? (engine.config['version'] as Map)['app'] ?? '' : ''}',
                style: TextStyle(
                  fontFamily: 'Consolas',
                  fontSize: 10.5,
                  height: 1.2,
                  color: t.textSecondary,
                ),
              ),
              const Spacer(),
              GhostButton(
                key: const ValueKey('update-check'),
                label: engine.updateChecking ? '检查中…' : '检查更新',
                icon: Icons.refresh_rounded,
                dense: true,
                onTap: busy ? () {} : () => engine.checkUpdate(),
              ),
            ],
          ),
          if (engine.updateError != null) ...[
            const SizedBox(height: 6),
            Text(
              engine.updateError!,
              style: TextStyle(fontSize: 10, height: 1.3, color: t.portNegative),
            ),
          ],
          if (engine.updateAvailable && info != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                Text(
                  'v${info['current']}  →  v${info['latest']}',
                  style: TextStyle(
                    fontFamily: 'Consolas',
                    fontSize: 13,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                    color: t.textPrimary,
                  ),
                ),
                const SizedBox(width: 10),
                TinyLabel(
                  '安装包 ${formatBytes((info['asset'] is Map ? (info['asset'] as Map)['size'] as num? : 0)?.toInt() ?? 0)}',
                  color: t.textTertiary,
                  size: 9,
                ),
                const Spacer(),
                GhostButton(
                  key: const ValueKey('update-apply'),
                  label: engine.updateApplying
                      ? '正在安装…'
                      : (engine.updateDownloading ? '下载中…' : '立即更新'),
                  icon: Icons.system_update_alt_rounded,
                  dense: true,
                  active: true,
                  onTap: busy ? () {} : () { _run(); },
                ),
              ],
            ),
            if (notes.isNotEmpty) ...[
              const SizedBox(height: 8),
              WellBox(
                padding: const EdgeInsets.fromLTRB(9, 7, 9, 7),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 132),
                  child: SingleChildScrollView(
                    child: SelectableText(
                      notes,
                      style: TextStyle(
                        fontFamily: 'Consolas',
                        fontSize: 10,
                        height: 1.45,
                        color: t.textSecondary,
                      ),
                    ),
                  ),
                ),
              ),
            ],
            if (engine.updateStage.isNotEmpty) ...[
              const SizedBox(height: 8),
              _UpdateProgress(engine: engine),
            ],
            const SizedBox(height: 4),
            Text(
              '关窗后自动替换并重新启动；config.json、config 与 modules 不会被覆盖',
              style: TextStyle(fontSize: 9.5, height: 1.3, color: t.textTertiary),
            ),
          ],
        ],
      ),
    );
  }
}

class _Rows extends StatelessWidget {
  const _Rows({required this.children});

  final List<Widget> children;

  // 平铺：内容有多高卡片就多高，不再套一层内滚动。
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(12, 6, 12, 6),
    child: Column(children: children),
  );
}

class _SwitchRow extends StatelessWidget {
  const _SwitchRow({
    super.key,
    required this.label,
    required this.desc,
    required this.on,
    required this.onTap,
  });

  final String label;
  final String desc;
  final bool on;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11.5,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                    color: t.textSecondary,
                  ),
                ),
                if (desc.isNotEmpty)
                  Text(
                    desc,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9.5,
                      height: 1.2,
                      color: t.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
          SwitchPill(on: on, onTap: onTap),
        ],
      ),
    );
  }
}

class _IntRow extends StatelessWidget {
  const _IntRow({
    required this.label,
    required this.value,
    required this.onStep,
  });

  final String label;
  final int value;
  final ValueChanged<int> onStep;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 11.5,
                height: 1.2,
                color: t.textSecondary,
              ),
            ),
          ),
          StepperField(
            width: 104,
            value: value,
            onCommit: (v) => onStep(v.clamp(1, 65535)),
            // StepperField 交回的是 ±1 增量，端口号要自己加并夹在合法区间。
            onStep: (d) => onStep((value + d).clamp(1, 65535)),
          ),
        ],
      ),
    );
  }
}

class _TextRow extends StatefulWidget {
  const _TextRow({
    required this.label,
    required this.desc,
    required this.value,
    required this.onCommit,
  });

  final String label;
  final String desc;
  final String value;
  final ValueChanged<String> onCommit;

  @override
  State<_TextRow> createState() => _TextRowState();
}

class _TextRowState extends State<_TextRow> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.value,
  );
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    // 没有确认键：敲完移开焦点就落盘，回车同样落盘。
    _focus.addListener(() {
      if (!_focus.hasFocus && _ctl.text != widget.value) {
        widget.onCommit(_ctl.text);
      }
    });
  }

  @override
  void didUpdateWidget(_TextRow old) {
    super.didUpdateWidget(old);
    // 引擎回传同一次编辑的结果时不要覆盖正在输入的内容。
    if (old.value != widget.value && !_focus.hasFocus) _ctl.text = widget.value;
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  widget.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11.5,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                    color: t.textSecondary,
                  ),
                ),
                if (widget.desc.isNotEmpty)
                  Text(
                    widget.desc,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9.5,
                      height: 1.2,
                      color: t.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
          SizedBox(
            // 与数字输入框同高：矮一格就盖不住里面的文字，看着像没对齐。
            width: 190,
            height: fieldHeight,
            child: WellBox(
              padding: const EdgeInsets.symmetric(horizontal: 7),
              child: singleLineBox(
                TextField(
                  controller: _ctl,
                  focusNode: _focus,
                  maxLines: 1,
                  textAlignVertical: TextAlignVertical.center,
                  onSubmitted: widget.onCommit,
                  style: fieldTextStyle(
                    t.textPrimary,
                    fontSize: 10.5,
                    fontFamily: 'Consolas',
                  ),
                  strutStyle: fieldStrut(
                    fontSize: 10.5,
                    fontFamily: 'Consolas',
                  ),
                  decoration: InputDecoration(
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                    border: InputBorder.none,
                    hintText: widget.label,
                    hintStyle: fieldHintStyle(
                      t.textTertiary,
                      fontSize: 10.5,
                      fontFamily: 'Consolas',
                    ),
                  ),
                ),
                fontSize: 10.5,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _PathRow extends StatelessWidget {
  const _PathRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 84,
          child: Text(
            label,
            style: TextStyle(
              fontSize: 11,
              height: 1.3,
              fontWeight: FontWeight.w600,
              color: t.textSecondary,
            ),
          ),
        ),
        Expanded(
          child: Text(
            value.isEmpty ? '—' : value,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontFamily: 'Consolas',
              fontSize: 10,
              height: 1.4,
              color: t.textTertiary,
            ),
          ),
        ),
      ],
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        SizedBox(
          width: 58,
          child: TinyLabel(label, color: t.textTertiary, size: 9),
        ),
        Expanded(
          child: Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 11, height: 1.2, color: t.textSecondary),
          ),
        ),
      ],
    );
  }
}
