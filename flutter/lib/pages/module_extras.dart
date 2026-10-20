import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../chrome/primitives.dart';
import '../engine/engine_models.dart';
import '../engine/engine_state.dart';

/// 详情页的两条附加行：第三方依赖是否齐、游戏模组往哪个目录装。
///
/// 两条都是旧界面上有、迁移时漏掉的：依赖不齐时模块点了「启动」只会在日志里
/// 报错，看着就像按钮没反应；游戏模组则是 Alice in Cradle 唯一的安装入口。
class ModuleExtras extends StatelessWidget {
  const ModuleExtras({super.key, required this.module, required this.engine});

  final ModuleInfo module;
  final StudioEngine engine;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final rows = [
      for (final r in (engine.moduleDeps['rows'] as List? ?? const []))
        if (r is Map) Map<String, Object?>.from(r),
    ];
    // 默认配置是启用时注入的：模块没启用时既没注入也没得套，
    // 那颗「一键套用」点了只会报错，直接不给。
    final defaults = module.enabled ? module.defaultsValue : null;
    if (rows.isEmpty &&
        engine.moduleMods['declared'] != true &&
        defaults == null) {
      return const SizedBox.shrink();
    }
    final missing = [
      for (final m in (engine.moduleDeps['missing'] as List? ?? const [])) '$m',
    ];
    final abiOk = engine.moduleDeps['abi_ok'] != false;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (rows.isNotEmpty) ...[
          // 说清这些包是模块要求额外装的、声明在哪儿：以前只有一排 ✓/✗ 的包名，
          // 没人知道它们是干什么的、少装了会怎样。
          TinyLabel(
            '额外 pip 包'
            '${(engine.moduleDeps['source'] as String? ?? '').isEmpty ? '' : ' · 声明在 ${engine.moduleDeps['source']}'}'
            '（缺少时模块启动会报错，可点下面的「安装依赖」）',
            color: t.textTertiary,
            size: 9,
          ),
          const SizedBox(height: 5),
          Wrap(
            spacing: 10,
            runSpacing: 4,
            children: [
              for (final r in rows)
                Row(
                  children: [
                    Text(
                      r['ok'] == true
                          ? '✓'
                          : (r['optional'] == true ? '○' : '✗'),
                      style: TextStyle(
                        fontSize: 10.5,
                        height: 1.2,
                        color: r['ok'] == true
                            ? t.portPositive
                            : (r['optional'] == true
                                  ? t.textTertiary
                                  : t.portNegative),
                      ),
                    ),
                    const SizedBox(width: 4),
                    Text(
                      '${r['req']}${r['optional'] == true ? '（可选）' : ''}',
                      style: TextStyle(
                        fontFamily: 'Consolas',
                        fontSize: 10,
                        height: 1.2,
                        color: t.textSecondary,
                      ),
                    ),
                  ],
                ),
            ],
          ),
          if (!abiOk)
            Padding(
              padding: const EdgeInsets.only(top: 3),
              child: TinyLabel(
                '内置 Python 升级过，_deps 需要整目录重装',
                color: t.portNegative,
                size: 9,
              ),
            ),
          if (missing.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 5),
              child: GhostButton(
                key: ValueKey('deps-install-${module.id}'),
                label: engine.isBusy(module.id) ? '安装中…' : '安装依赖',
                dense: true,
                active: true,
                onTap: () =>
                    engine.runExtrasTask(module.id, 'modules.deps_install'),
              ),
            ),
        ],
        if (engine.moduleMods['declared'] == true) ...[
          const SizedBox(height: 8),
          _ModsRow(module: module, engine: engine),
        ],
        // 模块自带的默认事件流 / 按键映射：装载时自动登记为可选配置，
        // 这里给一个「一键套用」的入口（删掉了也能补回来）。
        if (defaults != null) ...[
          const SizedBox(height: 8),
          _DefaultsRow(module: module, engine: engine, names: defaults),
        ],
      ],
    );
  }
}

/// 默认配置一行：列出模块带了哪几套，给「一键套用」把当前配置切成它。
class _DefaultsRow extends StatefulWidget {
  const _DefaultsRow({
    required this.module,
    required this.engine,
    required this.names,
  });

  final ModuleInfo module;
  final StudioEngine engine;
  final List<String> names;

  @override
  State<_DefaultsRow> createState() => _DefaultsRowState();
}

class _DefaultsRowState extends State<_DefaultsRow> {
  bool _busy = false;

  Future<void> _apply() async {
    if (_busy) return;
    setState(() => _busy = true);
    final result = await widget.engine.invoke('modules.apply_defaults', {
      'module_id': widget.module.id,
    });
    if (!mounted) return;
    setState(() => _busy = false);
    if (result is Map) {
      final notes = [
        for (final n in (result['notes'] as List? ?? const [])) '$n',
      ];
      widget.engine.say(
        notes.isEmpty ? '已套用 ${widget.module.name} 的默认配置' : notes.join('；'),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      children: [
        TinyLabel('默认配置', color: t.textTertiary),
        const SizedBox(width: 7),
        Expanded(
          child: Text(
            widget.names.join(' · '),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontFamily: 'Consolas',
              fontSize: 10,
              height: 1.2,
              color: t.textSecondary,
            ),
          ),
        ),
        const SizedBox(width: 6),
        GhostButton(
          key: ValueKey('apply-defaults-${widget.module.id}'),
          label: _busy ? '套用中…' : '一键套用',
          dense: true,
          onTap: _apply,
        ),
      ],
    );
  }
}

class _ModsRow extends StatefulWidget {
  const _ModsRow({required this.module, required this.engine});

  final ModuleInfo module;
  final StudioEngine engine;

  @override
  State<_ModsRow> createState() => _ModsRowState();
}

class _ModsRowState extends State<_ModsRow> {
  late final TextEditingController _ctl = TextEditingController(
    text: '${widget.engine.moduleMods['root'] ?? ''}',
  );
  final FocusNode _focus = FocusNode();
  String _shown = '';

  @override
  void initState() {
    super.initState();
    _shown = _ctl.text;
  }

  @override
  void didUpdateWidget(_ModsRow old) {
    super.didUpdateWidget(old);
    final root = '${widget.engine.moduleMods['root'] ?? ''}';
    // 只在引擎这一侧真的换了目录时回写，正敲着的时候不动它。
    if (root != _shown) {
      _shown = root;
      if (!_focus.hasFocus) _ctl.text = root;
    }
  }

  @override
  void dispose() {
    _focus.dispose();
    _ctl.dispose();
    super.dispose();
  }

  Future<void> _scan(AppTokens t) async {
    final engine = widget.engine;
    await engine.runExtrasTask(widget.module.id, 'modules.mods_scan');
    if (!mounted) return;
    final found = engine.moduleModsFound;
    if (found.isEmpty) {
      engine.say('未扫描到游戏目录，可手动输入后点安装');
      return;
    }
    // 扫到多处时挑一处，选中的目录写进上面那一格。
    final palette = AppScope.colors(context);
    final picked = found.length == 1
        ? found.first
        : await showDialog<String>(
            context: context,
            builder: (_) => AppScope(
              tokens: t,
              palette: palette,
              child: _RootsDialog(roots: found),
            ),
          );
    if (picked != null && mounted) setState(() => _ctl.text = picked);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final engine = widget.engine;
    final busy = engine.isBusy(widget.module.id);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            TinyLabel('游戏模组', color: t.textTertiary),
            const SizedBox(width: 7),
            Expanded(
              child: InlineField(
                key: ValueKey('mods-root-${widget.module.id}'),
                controller: _ctl,
                focusNode: _focus,
                hint: '游戏根目录（含 BepInEx）',
                minWidth: 120,
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        Row(
          children: [
            Expanded(
              child: GhostButton(
                key: ValueKey('mods-scan-${widget.module.id}'),
                label: busy ? '扫描中…' : '自动扫描',
                dense: true,
                onTap: () => _scan(t),
              ),
            ),
            const SizedBox(width: 5),
            Expanded(
              child: GhostButton(
                key: ValueKey('mods-install-${widget.module.id}'),
                label: '安装游戏模组',
                dense: true,
                active: true,
                onTap: () => engine.runExtrasTask(
                  widget.module.id,
                  'modules.mods_install',
                  {'root': _ctl.text.trim()},
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// 扫到多个游戏目录时的选择框：路径很长，横向不铺开就会互相截断。
class _RootsDialog extends StatelessWidget {
  const _RootsDialog({required this.roots});

  final List<String> roots;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Dialog(
      backgroundColor: t.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusCard),
        side: BorderSide(color: t.edge),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560, maxHeight: 420),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TinyLabel('扫描到的游戏目录', color: t.textSecondary, size: 11),
              const SizedBox(height: 10),
              Expanded(
                child: ListView(
                  children: [
                    for (final root in roots)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: MouseRegion(
                          cursor: SystemMouseCursors.click,
                          child: GestureDetector(
                            behavior: HitTestBehavior.opaque,
                            onTap: () => Navigator.of(context).pop(root),
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 9,
                                vertical: 8,
                              ),
                              decoration: BoxDecoration(
                                color: t.wellDeep,
                                borderRadius: BorderRadius.circular(
                                  AppTokens.radiusField,
                                ),
                                border: Border.all(color: t.edge),
                              ),
                              child: Text(
                                root,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  fontFamily: 'Consolas',
                                  fontSize: 10.5,
                                  color: t.textPrimary,
                                ),
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: GhostButton(
                  label: '取消',
                  dense: true,
                  onTap: () => Navigator.of(context).pop(),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
