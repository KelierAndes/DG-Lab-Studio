import 'dart:async';

import 'package:flutter/material.dart' hide Stack;

import '../app/tokens.dart';
import '../chrome/nav_rail.dart';
import '../chrome/page_kit.dart';
import '../chrome/module_icons.dart';
import '../chrome/primitives.dart';
import '../engine/engine_models.dart';
import '../engine/engine_state.dart';
import 'actions.dart';
import 'app_pages.dart';
import 'detector_card.dart';
import 'module_extras.dart';

/// 模块：已安装模块的开关与设置，以及模块市场。
class ModulesPage extends StatelessWidget {
  const ModulesPage({
    super.key,
    required this.nav,
    required this.engine,
    required this.actions,
  });

  final AppNav nav;
  final StudioEngine engine;
  final StudioActions actions;

  ModuleInfo? get _selected {
    final id = nav.selectedModule;
    for (final m in engine.modules) {
      if (m.id == id) return m;
    }
    return engine.modules.isEmpty ? null : engine.modules.first;
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: Listenable.merge([nav, engine]),
      builder: (context, _) {
        final shown = [
          for (final m in engine.modules)
            if (nav.matches(m.name) || nav.matches(m.id)) m,
        ];
        final selected = _selected;
        if (selected != null) {
          // 设置面板按模块取；切模块时在帧尾补取，避免在 build 里发起请求。
          WidgetsBinding.instance.addPostFrameCallback((_) {
            // 设置卡只在启用时出现：没启用就不必把那份设置文件拉回来。
            if (selected.enabled) engine.loadSettings(selected.id);
            engine.loadExtras(selected.id);
          });
        }
        return PageBody(
          pages: nav,
          header: PageHeader(
            title: '模块',
            subtitle:
                '已启用 ${engine.modules.where((m) => m.enabled).length} / ${engine.modules.length} 个模块',
            pages: nav,
            actions: [
              // 右上角只留急停这一颗；「刷新市场」降到列表上方那一排。
              EstopButton(
                key: const ValueKey('modules-estop'),
                onTap: () => actions.invoke('engine.emergency_stop'),
              ),
            ],
          ),
          children: [
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Row(
                children: [
                  TinyLabel(
                    '启用模块会把它的默认配置注入进来，停用即收回',
                    color: t.textTertiary,
                    size: 9.5,
                  ),
                  const Spacer(),
                  OptionPill(
                    key: const ValueKey('modules-refresh'),
                    label: engine.marketLoading ? '取清单中…' : '刷新市场',
                    active: true,
                    dense: true,
                    onTap: () => engine.fetchMarket(),
                  ),
                ],
              ),
            ),
            // 详情页按自己的内容定高，已安装列表对齐到它：列表长就在卡内滚动，
            // 不再反过来把详情卡撑出一段空白（用户要的「对齐」是这个方向）。
            _AlignedPair(
              anchor: BentoCard(
                fit: true,
                title: selected == null ? '模块详情' : '详情 · ${selected.name}',
                child: selected == null
                    ? const Padding(
                        padding: EdgeInsets.all(12),
                        child: EmptyNote('选择一个模块查看它的参数与开关。'),
                      )
                    : _DetailCard(module: selected, engine: engine),
              ),
              follower: (bounded) => BentoCard(
                // 定了高度就走 Expanded 内容：列表比详情长时在卡内滚动。
                fit: !bounded,
                title: '已安装',
                accent: t.accent,
                trailing: CountChip(count: shown.length),
                child: SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(8, 4, 8, 4),
                  child: Column(
                    children: [
                      for (final m in shown)
                        TileRow(
                          key: ValueKey('module-${m.id}'),
                          selected: selected?.id == m.id,
                          onTap: () => nav.selectModule(m.id),
                          leading: ModuleTile(id: m.id, active: m.enabled),
                          title: m.name,
                          subtitle: '${m.id} · v${m.version}',
                          trailing: engine.isBusy(m.id)
                              ? const SizedBox(
                                  width: 14,
                                  height: 14,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : SwitchPill(
                                  on: m.enabled,
                                  onTap: () => engine.runModuleTask(
                                    m.id,
                                    'modules.set_enabled',
                                    {'enabled': !m.enabled},
                                  ),
                                ),
                        ),
                      if (shown.isEmpty) const EmptyNote('还没有安装模块，去下方市场看看。'),
                    ],
                  ),
                ),
              ),
            ),
            // 模块没启用时设置文件改了也不生效（启用才装载它），整张卡收掉；
            // 启用之后设置项与参数登记才跟着出现。
            if (selected != null && selected.enabled) ...[
              const SizedBox(height: 12),
              BentoCard(
                fit: true,
                title: '设置 · ${selected.name}',
                accent: t.portPositive,
                trailing: TinyLabel(
                  '改完点「重新加载」生效',
                  color: t.textTertiary,
                  size: 9,
                ),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(10, 4, 10, 6),
                  child: engine.settingsModuleId != selected.id
                      ? const Row(
                          children: [
                            SizedBox(width: 8),
                            SizedBox(
                              width: 13,
                              height: 13,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            ),
                            SizedBox(width: 10),
                            Expanded(child: Text('正在读取模块设置…')),
                          ],
                        )
                      : _SettingsList(engine: engine, module: selected),
                ),
              ),
              const SizedBox(height: 12),
            ],
            // 画面识别类模块的识别参数表：只有真正在跑实时检测的模块才有。
            if (selected != null &&
                selected.realtimeManager &&
                selected.loaded) ...[
              BentoCard(
                fit: true,
                title: '识别参数 · ${selected.name}',
                accent: t.portModel,
                trailing: TinyLabel(
                  '参数名即事件流里的变量',
                  color: t.textTertiary,
                  size: 9,
                ),
                child: DetectorCard(
                  key: ValueKey('detectors-${selected.id}'),
                  moduleId: selected.id,
                  engine: engine,
                ),
              ),
              const SizedBox(height: 12),
            ],
            // 参数登记表单独一张卡，与图像识别那类一样从模块配置里分出来。
            // 一个模块的多张表并排放在同一张卡里：OSC 的「设备参数」（只可改名）
            // 与「自定义参数」（全可改）就是同一件事的两种来源，分开看才对得上。
            if (selected != null &&
                selected.enabled &&
                engine.settingsModuleId == selected.id &&
                _listSpecs(engine).isNotEmpty) ...[
              BentoCard(
                fit: true,
                title: '参数登记 · ${selected.name}',
                accent: t.portModel,
                trailing: TinyLabel(
                  '名字即事件流里的变量',
                  color: t.textTertiary,
                  size: 9,
                ),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(10, 4, 10, 6),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final pair in _listSpecs(engine).indexed) ...[
                        if (pair.$1 > 0) const SizedBox(height: 14),
                        _SettingField(
                          key: ValueKey(
                            'setting-list-${selected.id}-${pair.$2.key}',
                          ),
                          moduleId: selected.id,
                          engine: engine,
                          fieldKey: pair.$2.key,
                          item: pair.$2.value,
                          value: engine.moduleSettings[pair.$2.key],
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
            ],
            BentoCard(
              fit: true,
              title: '模块市场',
              accent: t.portImage,
              trailing: TinyLabel(
                '清单来自 dgstudio-modules-market',
                color: t.textTertiary,
                size: 9,
              ),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(8, 4, 8, 4),
                child: Column(
                  children: [
                    for (final m in engine.market)
                      TileRow(
                        key: ValueKey('market-${m['id']}'),
                        leading: ThumbMark(size: 28, seed: t.accent),
                        title: '${m['name'] ?? m['id']}',
                        subtitle: '${m['description'] ?? ''}',
                        trailing: _MarketButton(
                          engine: engine,
                          id: '${m['id']}',
                          installed: m['installed'] == true,
                          update: m['update'] == true,
                        ),
                      ),
                    if (engine.marketLoading)
                      const Padding(
                        padding: EdgeInsets.fromLTRB(9, 8, 9, 8),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 13,
                              height: 13,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            ),
                            SizedBox(width: 9),
                            Expanded(
                              child: Text('正在从 dgstudio-modules-market 取清单…'),
                            ),
                          ],
                        ),
                      ),
                    if (!engine.marketLoading && engine.market.isEmpty)
                      EmptyNote(engine.marketError ?? '市场清单还没取到，点右上角「刷新市场」。'),
                  ],
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _DetailCard extends StatelessWidget {
  const _DetailCard({required this.module, required this.engine});

  final ModuleInfo module;
  final StudioEngine engine;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            module.description,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 11, height: 1.4, color: t.textSecondary),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              StatusChip(
                text: module.running ? '运行中' : (module.enabled ? '已启用' : '已停用'),
                tone: module.running ? 1 : (module.enabled ? 3 : 0),
              ),
              const SizedBox(width: 7),
              StatusChip(text: 'v${module.version}', tone: 0),
              if (engine.isBusy(module.id)) ...[
                const SizedBox(width: 9),
                const SizedBox(
                  width: 11,
                  height: 11,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                const SizedBox(width: 6),
                TinyLabel('处理中', color: t.textTertiary, size: 9),
              ],
              const Spacer(),
              // 模块自带的说明文档：装之前 / 出问题时要能就地读，不用去翻仓库。
              GhostButton(
                key: ValueKey('module-readme-${module.id}'),
                label: 'README',
                icon: Icons.description_outlined,
                dense: true,
                onTap: () => _showReadme(context, engine, module),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // 依赖与游戏模组：模块跑不起来时先看这一行缺了什么。
          ModuleExtras(
            key: ValueKey('extras-${module.id}'),
            module: module,
            engine: engine,
          ),
          const SizedBox(height: 10),
          // 动作清单平铺出来：卡片按内容长，不再在里面套滚动条。
          WellBox(
            padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final a in module.actions)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 5),
                    child: Text(
                      '· $a',
                      style: TextStyle(
                        fontSize: 10.5,
                        height: 1.3,
                        color: t.textTertiary,
                      ),
                    ),
                  ),
                if (module.actions.isEmpty)
                  Text(
                    '这个模块没有声明额外动作。',
                    style: TextStyle(
                      fontSize: 10.5,
                      height: 1.3,
                      color: t.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          _ModuleActions(module: module, engine: engine),
        ],
      ),
    );
  }
}

/// 详情卡的三个动作：重新加载 / 启停 / 卸载。
///
/// 卸载是这一排里唯一会删文件的那颗，所以给它一次反悔：与「清空本页」同一套
/// 双击确认，点第一下只把按钮变成「再点一次确认卸载」。
class _ModuleActions extends StatefulWidget {
  const _ModuleActions({required this.module, required this.engine});

  final ModuleInfo module;
  final StudioEngine engine;

  @override
  State<_ModuleActions> createState() => _ModuleActionsState();
}

class _ModuleActionsState extends State<_ModuleActions> {
  bool _confirm = false;
  Timer? _fade;

  @override
  void dispose() {
    _fade?.cancel();
    super.dispose();
  }

  void _uninstall() {
    if (!_confirm) {
      setState(() => _confirm = true);
      _fade?.cancel();
      _fade = Timer(const Duration(seconds: 5), () {
        if (mounted) setState(() => _confirm = false);
      });
      return;
    }
    _fade?.cancel();
    setState(() => _confirm = false);
    unawaited(widget.engine.deleteModule(widget.module.id));
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final engine = widget.engine;
    final module = widget.module;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: GhostButton(
                key: ValueKey('module-reload-${module.id}'),
                label: '重新加载',
                dense: true,
                onTap: () => engine.runModuleTask(module.id, 'modules.reload'),
              ),
            ),
            const SizedBox(width: 6),
            // 启用与停用是成对的：启用要走 install（装依赖 + 起实例 + 注入配置档），
            // 只 set_enabled 的话模块读进来但不跑，看着就像按钮没反应。
            Expanded(
              child: GhostButton(
                key: ValueKey('module-toggle-${module.id}'),
                label: module.running ? '停止' : '启动',
                dense: true,
                active: !module.running,
                onTap: () => engine.runModuleTask(
                  module.id,
                  module.running ? 'modules.deactivate' : 'modules.install',
                ),
              ),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: GhostButton(
                key: ValueKey('module-uninstall-${module.id}'),
                // 三个按钮各占三分之一宽，标签得和「重新加载」一样是四个字，
                // 长一句就溢出（说明写在下面那一行）。
                label: _confirm ? '确认卸载' : '卸载',
                dense: true,
                active: _confirm,
                onTap: _uninstall,
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        TinyLabel(
          _confirm
              ? '再点一次就删掉 ${module.id} 的模块文件并收回它注入的默认配置；'
                    'config 里那份设置保留，重装即恢复。'
              : '「启动 / 停止」只管运行；「卸载」连模块文件一起删，市场恢复成「安装」。',
          color: _confirm ? t.portNegative : t.textTertiary,
          size: 9,
        ),
      ],
    );
  }
}

class _MarketButton extends StatelessWidget {
  const _MarketButton({
    required this.engine,
    required this.id,
    required this.installed,
    required this.update,
  });

  final StudioEngine engine;
  final String id;
  final bool installed;

  /// 装着的版本比市场里旧才算「更新」：同版本再给一颗按钮，点下去只是重装一遍。
  final bool update;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    if (engine.isBusy(id)) {
      return const SizedBox(
        width: 14,
        height: 14,
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    }
    if (update) {
      return GhostButton(
        key: ValueKey('market-btn-$id'),
        label: '更新',
        dense: true,
        active: true,
        onTap: () => engine.runModuleTask(id, 'modules.update'),
      );
    }
    if (installed) {
      return TinyLabel('已安装', color: t.textTertiary, size: 9);
    }
    return GhostButton(
      key: ValueKey('market-btn-$id'),
      label: '安装',
      dense: true,
      onTap: () => engine.runModuleTask(id, 'market.install'),
    );
  }
}

/// 模块声明的列表型设置项（OSC 的两张登记表、识别模块的参数表都算）。
List<MapEntry<String, Map<String, Object?>>> _listSpecs(StudioEngine engine) => [
  for (final e in engine.moduleSpec.entries)
    if (e.value is Map && '${(e.value as Map)['type']}' == 'list')
      MapEntry(e.key, Map<String, Object?>.from(e.value as Map)),
];

class _SettingsList extends StatelessWidget {
  const _SettingsList({required this.engine, required this.module});

  final StudioEngine engine;
  final ModuleInfo module;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    // 列表型设置项（如 OSC 的自定义参数）不塞在设置卡里：单独成卡，与识别参数同样待遇。
    final rows = engine.moduleSpec.entries
        .where((e) => (e.value as Map<String, Object?>)['type'] != 'list')
        .toList();
    if (rows.isEmpty) {
      return Center(
        child: Text(
          '该模块没有额外设置项。',
          style: TextStyle(fontSize: 11, height: 1, color: t.textTertiary),
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final e in rows)
          Padding(
            padding: const EdgeInsets.only(bottom: 9),
            child: _SettingField(
              key: ValueKey('setting-${module.id}-${e.key}'),
              moduleId: module.id,
              engine: engine,
              fieldKey: e.key,
              item: e.value as Map<String, Object?>,
              value: engine.moduleSettings[e.key],
            ),
          ),
      ],
    );
  }
}

/// 一个设置项：控件类型由模块的 config_spec 决定，与旧界面同一套取值。
class _SettingField extends StatelessWidget {
  const _SettingField({
    super.key,
    required this.moduleId,
    required this.engine,
    required this.fieldKey,
    required this.item,
    required this.value,
  });

  final String moduleId;
  final StudioEngine engine;
  final String fieldKey;
  final Map<String, Object?> item;
  final Object? value;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final type = '${item['type'] ?? 'str'}';
    final label = '${item['label'] ?? fieldKey}';
    final desc = '${item['desc'] ?? ''}';

    // 列表型设置（OSC 的两张登记表）要整幅摆一张表，
    // 不能塞进「左边标签右边控件」那一行。
    if (type == 'list') {
      return _ListSetting(
        fieldKey: fieldKey,
        label: label,
        desc: desc,
        item: item,
        value: value,
        onCommit: (rows) => engine.setSetting(moduleId, fieldKey, rows),
        onRename: item['fixed'] == true
            ? (from, to) => engine.renameSettingVar(moduleId, from, to)
            : null,
      );
    }

    // 映射型设置（设备参数前缀那类）按每个键铺开成一行：折叠成「3 个键」就只
    // 看得见个数，改不了值，等于把这个设置项废了。
    if (type == 'map') {
      final map = value is Map
          ? Map<String, Object?>.from(value as Map)
          : const <String, Object?>{};
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
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
              style: TextStyle(fontSize: 9.5, height: 1.3, color: t.textTertiary),
            ),
          const SizedBox(height: 5),
          for (final e in map.entries)
            Padding(
              padding: const EdgeInsets.only(bottom: 7),
              child: Row(
                children: [
                  SizedBox(
                    width: 120,
                    child: Text(
                      '${_familyLabel(e.key)} · ${e.key}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 10.5,
                        height: 1.2,
                        color: t.textTertiary,
                      ),
                    ),
                  ),
                  Expanded(
                    child: Align(
                      alignment: Alignment.centerRight,
                      child: _TextRow(
                        text: '${e.value ?? ''}',
                        hint: e.key,
                        onCommit: (v) => engine.setSetting(
                          moduleId,
                          fieldKey,
                          {...map, e.key: v},
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          if (map.isEmpty)
            TinyLabel('这个映射还是空的。', color: t.textTertiary, size: 9.5),
        ],
      );
    }

    final control = switch (type) {
      'bool' => SwitchPill(
        on: value == true,
        onTap: () => engine.setSetting(moduleId, fieldKey, !(value == true)),
      ),
      'int' => StepperField(
        width: 92,
        value: _num(0).round(),
        onCommit: (v) {
          final low = (item['min'] as num?)?.round() ?? 0;
          final high = (item['max'] as num?)?.round() ?? 999999;
          engine.setSetting(moduleId, fieldKey, v.clamp(low, high));
        },
        // StepperField 交回来的是 ±1 增量，不是新值；这里自己加并夹到 spec 的范围。
        onStep: (d) {
          final low = (item['min'] as num?)?.round() ?? 0;
          final high = (item['max'] as num?)?.round() ?? 999999;
          engine.setSetting(
            moduleId,
            fieldKey,
            (_num(0).round() + d).clamp(low, high),
          );
        },
      ),
      'float' => _NumRow(
        value: _num(0),
        min: (item['min'] as num?)?.toDouble() ?? 0,
        max: (item['max'] as num?)?.toDouble() ?? 100,
        step: (item['step'] as num?)?.toDouble() ?? .1,
        unit: '${item['unit'] ?? ''}',
        onCommit: (v) => engine.setSetting(moduleId, fieldKey, v),
      ),
      'choice' => PickerField(
        width: 150,
        compact: true,
        value: '${value ?? ''}',
        options: [for (final c in (item['choices'] as List? ?? const [])) '$c'],
        onChanged: (v) => engine.setSetting(moduleId, fieldKey, v),
      ),
      'param' || 'str' => _TextRow(
        text: '${value ?? item['default'] ?? ''}',
        hint: desc.isNotEmpty ? desc : fieldKey,
        onCommit: (v) => engine.setSetting(moduleId, fieldKey, v),
      ),
      _ => SizedBox(
        width: 150,
        child: Text(
          _preview(value),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 10.5, height: 1.3, color: t.textTertiary),
        ),
      ),
    };

    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
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
        control,
      ],
    );
  }

  double _num(double fallback) {
    if (value is num) return (value as num).toDouble();
    if (item['default'] is num) return (item['default'] as num).toDouble();
    return fallback;
  }

  static String _preview(Object? v) {
    if (v is List) return '共 ${v.length} 项（列表项暂只读）';
    if (v is Map) return '${v.length} 个键（复杂项暂只读）';
    return '$v';
  }

  /// 设备家族键的中文名：前缀那一行光写 COYOTE 看不出改的是哪台设备的地址。
  static String _familyLabel(String key) => switch (key.toUpperCase()) {
    'COYOTE' => '郊狼',
    'OVC' => '负鼠',
    'BMTR' => '灵猫',
    _ => key,
  };
}

/// 列表体一行的实测高度与最多露出的行数。
const double listRowHeight = 36;
const int listVisibleRows = 5;

/// 列表型设置项：按模块声明的 `columns` 渲染一张表。
///
/// OSC 的两张表都靠它——「设备参数」是 `fixed` 的：行由设备决定，只能改名字
/// （改名走引擎那条改名，收发地址与画布上的卡片一起改接）；「自定义参数」整行
/// 可增可改，填一条完整地址即建立变量。整表一次写回（`modules.settings_set` 收任意 JSON）。
/// 表体最多露出 `listVisibleRows` 行，其余在卡内滚动。
class _ListSetting extends StatefulWidget {
  const _ListSetting({
    required this.fieldKey,
    required this.label,
    required this.desc,
    required this.item,
    required this.value,
    required this.onCommit,
    this.onRename,
  });

  final String fieldKey;
  final String label;
  final String desc;
  final Map<String, Object?> item;
  final Object? value;
  final void Function(List<Map<String, Object?>> rows) onCommit;

  /// 固定表那一栏的改名回调：交回来的是一行的旧名与新名，不是整张表。
  final Future<void> Function(String from, String to)? onRename;

  @override
  State<_ListSetting> createState() => _ListSettingState();
}

class _ListSettingState extends State<_ListSetting> {
  late List<Map<String, Object?>> _rows = _copy(widget.value);
  String? _error;

  static List<Map<String, Object?>> _copy(Object? value) => [
    for (final r in (value as List? ?? const []))
      if (r is Map) Map<String, Object?>.from(r),
  ];

  List<Map<String, Object?>> get _columns => [
    for (final c in (widget.item['columns'] as List? ?? const []))
      if (c is Map) Map<String, Object?>.from(c),
  ];

  String _lastSent = '';

  static String _keyed(List<Map<String, Object?>> rows) =>
      rows.map((r) => '$r').join('|');

  @override
  void initState() {
    super.initState();
    _lastSent = _keyed(_rows);
  }

  @override
  void didUpdateWidget(_ListSetting old) {
    super.didUpdateWidget(old);
    // 只在引擎回传的表跟我们最后一次写回的不一致时刷新：
    // 否则用户正在敲的那一行会被上一帧的旧表盖掉。
    final incoming = _copy(widget.value);
    if (_keyed(incoming) == _lastSent) return;
    setState(() => _rows = incoming);
    _lastSent = _keyed(incoming);
  }

  void _commit() {
    final first = _columns.isEmpty ? 'address' : '${_columns.first['id']}';
    final kept = [
      for (final r in _rows)
        if ('${r[first] ?? ''}'.trim().isNotEmpty) r,
    ];
    _lastSent = _keyed(kept);
    widget.onCommit(kept);
  }

  void _add() {
    setState(
      () => _rows = [
        ..._rows,
        {
          for (final c in _columns)
            '${c['id']}': c['type'] == 'choice'
                ? (c['choices'] as List?)?.first ?? ''
                : '',
        },
      ],
    );
  }

  void _remove(int index) {
    setState(() => _rows = [..._rows]..removeAt(index));
    _commit();
  }

  bool get _fixed => widget.item['fixed'] == true;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final cols = _columns;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          widget.label,
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
            style: TextStyle(fontSize: 9.5, height: 1.3, color: t.textTertiary),
          ),
        const SizedBox(height: 6),
        // 一张表最多露出 5 行，其余在卡内滚动：设备一连上就是几十条参数，
        // 全平铺会把整页拉成几屏高，找不到下面那张表。
        ConstrainedBox(
          constraints: const BoxConstraints(
            maxHeight: listRowHeight * listVisibleRows,
          ),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (var i = 0; i < _rows.length; i++)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Row(
                      children: [
                        for (final c in cols) ...[
                          Expanded(
                            flex: c['type'] == 'choice' ? 1 : 3,
                            child: _cell(c, i),
                          ),
                          const SizedBox(width: 6),
                        ],
                        // 固定表（设备自动登记那一类）不给增删：行由设备决定。
                        if (!_fixed)
                          IconSquare(
                            key: ValueKey('list-remove-${widget.fieldKey}-$i'),
                            icon: Icons.remove_rounded,
                            size: 26,
                            iconSize: 13,
                            onTap: () => _remove(i),
                          ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
        if (_rows.isEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: TinyLabel(
              _fixed
                  ? '设备连上后自动登记在这里，这里只改名字。'
                  : '还没有一条：点下面的「添加一行」填入完整 OSC 地址',
              color: t.textTertiary,
              size: 9.5,
            ),
          ),
        if (!_fixed)
          Row(
            children: [
              GhostButton(
                key: ValueKey('list-add-${widget.fieldKey}'),
                label: '添加一行',
                icon: Icons.add_rounded,
                dense: true,
                onTap: _add,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _error ?? '改完即写回；地址就是事件流里的变量名',
                  style: TextStyle(
                    fontSize: 9.5,
                    height: 1.2,
                    color: t.textTertiary,
                  ),
                ),
              ),
            ],
          )
        else
          Text(
            _error ?? '这一栏由设备决定：只改名字，类型与读写改不得',
            style: TextStyle(
              fontSize: 9.5,
              height: 1.2,
              color: _error == null ? t.textTertiary : t.portNegative,
            ),
          ),
      ],
    );
  }

  Widget _cell(Map<String, Object?> col, int index) {
    final t = AppScope.of(context);
    final id = '${col['id']}';
    final key = ValueKey('list-cell-${widget.fieldKey}-$id-$index');
    final options = [
      for (final o in (col['choices'] as List? ?? const [])) '$o',
    ];
    final labels = [
      for (final o in (col['labels'] as List? ?? const [])) '$o',
    ];
    final value = '${_rows[index][id] ?? ''}';
    // 只读列（设备参数的读写 / 类型）：由设备决定，写成文字给人看而不是给人改。
    if (col['readonly'] == true) {
      final shown = labels.isNotEmpty && options.contains(value)
          ? labels[options.indexOf(value)]
          : (value.isEmpty ? '—' : value);
      return Align(
        key: key,
        alignment: Alignment.centerLeft,
        child: Text(
          shown,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 10.5, height: 1, color: t.textSecondary),
        ),
      );
    }
    if (col['type'] != 'choice') {
      return _ListTextCell(
        key: key,
        text: value,
        hint: '${col['hint'] ?? col['label'] ?? id}',
        onCommit: (text) {
          final next = text.trim();
          if (next == value) return;
          // 固定表（设备参数）只改名字：交回引擎那条改名，本地不留半改的状态。
          if (_fixed) {
            setState(
              () => _rows = [
                for (var i = 0; i < _rows.length; i++)
                  if (i == index) {..._rows[i], id: next} else _rows[i],
              ],
            );
            // 必须把「最后一次本地值」记下来：不记的话引擎回传的那张表还等于
            // 初始值，会被当成「和上次一样」丢掉，改名的结果（成没成）就上不了屏。
            _lastSent = _keyed(_rows);
            unawaited(widget.onRename?.call(value, next));
            return;
          }
          setState(
            () => _rows = [
              for (var i = 0; i < _rows.length; i++)
                if (i == index) {..._rows[i], id: next} else _rows[i],
            ],
          );
          _commit();
        },
      );
    }
    {
      return PickerField(
        key: key,
        width: 78,
        compact: true,
        value: labels.isNotEmpty && options.contains(value)
            ? labels[options.indexOf(value)]
            : value,
        options: labels.isNotEmpty ? labels : options,
        onChanged: (label) {
          final at = labels.isEmpty
              ? options.indexOf(label)
              : labels.indexOf(label);
          setState(
            () => _rows = [
              for (var i = 0; i < _rows.length; i++)
                if (i == index) {..._rows[i], id: options[at]} else _rows[i],
            ],
          );
          _commit();
        },
      );
    }
  }
}

/// 一格地址：控制器归这一格自己管。放在父级 build 里现造会把正在敲的字和焦点一起弄丢。
class _ListTextCell extends StatefulWidget {
  const _ListTextCell({
    super.key,
    required this.text,
    required this.hint,
    required this.onCommit,
  });

  final String text;
  final String hint;
  final ValueChanged<String> onCommit;

  @override
  State<_ListTextCell> createState() => _ListTextCellState();
}

class _ListTextCellState extends State<_ListTextCell> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.text,
  );
  final FocusNode _focus = FocusNode();

  /// 已经交给引擎的文本：引擎回传的表要是按这份改的，就照它覆盖，
  /// 哪怕光标还在这一格里（改名的成没成得让用户看见结果）。
  String? _sent;

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!_focus.hasFocus) _send();
    });
  }

  void _send() {
    final text = _ctl.text.trim();
    // 同一份文本只交一次：回车与失焦两个来源会撞在一起，重复提交会把改名
    // 打到第二次（那时原名已经不存在了，引擎只能回一句「找不到」）。
    if (text == widget.text || text == _sent) return;
    _sent = text;
    widget.onCommit(_ctl.text);
  }

  @override
  void didUpdateWidget(_ListTextCell old) {
    super.didUpdateWidget(old);
    // 判据是「格子里的字 vs 引擎那份」，不是「上一次的 widget vs 这一次的」：
    // 提交与回传可能落在同一帧里，那时 old.text 根本没变过，格子会一直停在
    // 用户敲的半成品上（改名失败也看不到收回）。
    if (_ctl.text == widget.text) return;
    if (!_focus.hasFocus || _ctl.text.trim() == _sent) _ctl.text = widget.text;
  }

  @override
  void dispose() {
    _ctl.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return InlineField(
      controller: _ctl,
      focusNode: _focus,
      hint: widget.hint,
      minWidth: 120,
      onCommit: (_) => _send(),
    );
  }
}

class _NumRow extends StatelessWidget {
  const _NumRow({
    required this.value,
    required this.min,
    required this.max,
    required this.step,
    required this.unit,
    required this.onCommit,
  });

  final double value;
  final double min;
  final double max;
  final double step;
  final String unit;
  final ValueChanged<double> onCommit;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconSquare(
          icon: Icons.remove_rounded,
          size: 24,
          iconSize: 13,
          onTap: () => onCommit((value - step).clamp(min, max)),
        ),
        SizedBox(
          width: 62,
          child: Text(
            '${value.toStringAsFixed(step >= 1 ? 0 : 1)}$unit',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 11.5,
              height: 1.6,
              letterSpacing: .1,
              color: t.textPrimary,
            ),
          ),
        ),
        IconSquare(
          icon: Icons.add_rounded,
          size: 24,
          iconSize: 13,
          onTap: () => onCommit((value + step).clamp(min, max)),
        ),
      ],
    );
  }
}

class _TextRow extends StatefulWidget {
  const _TextRow({
    required this.text,
    required this.hint,
    required this.onCommit,
  });

  final String text;
  final String hint;
  final ValueChanged<String> onCommit;

  @override
  State<_TextRow> createState() => _TextRowState();
}

class _TextRowState extends State<_TextRow> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.text,
  );

  @override
  void didUpdateWidget(_TextRow old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text) _ctl.text = widget.text;
  }

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return InlineField(
      // 确认键去掉：敲完移开焦点（或回车）就写回，与数字框同一高度。
      controller: _ctl,
      hint: widget.hint,
      minWidth: 150,
      onCommit: widget.onCommit,
      suffix: Icon(
        Icons.edit_rounded,
        size: 11,
        color: AppScope.of(context).textTertiary,
      ),
    );
  }
}

/// 两栏同底，但由「锚」那一栏定高：跟随栏对齐到锚的高度，内容更长就在自己内部滚动。
/// 模块页要的是「已安装列表对齐详情页」，用 IntrinsicHeight 会变成反向（详情被列表撑高）。
class _AlignedPair extends StatefulWidget {
  const _AlignedPair({required this.anchor, required this.follower});

  final Widget anchor;

  /// `bounded` 为真时跟随栏拿到确定高度，卡片要走 Expanded 内容 + 内部滚动。
  final Widget Function(bool bounded) follower;

  @override
  State<_AlignedPair> createState() => _AlignedPairState();
}

class _AlignedPairState extends State<_AlignedPair> {
  final GlobalKey _anchorKey = GlobalKey();
  double? _height;

  void _measure() {
    if (!mounted) return;
    final box = _anchorKey.currentContext?.findRenderObject();
    final h = box is RenderBox ? box.size.height : null;
    if (h == null || h <= 0 || (h - (_height ?? -1)).abs() < .5) return;
    setState(() => _height = h);
  }

  @override
  Widget build(BuildContext context) {
    WidgetsBinding.instance.addPostFrameCallback((_) => _measure());
    final bounded = _height != null;
    final anchor = KeyedSubtree(key: _anchorKey, child: widget.anchor);
    final follower = widget.follower(bounded);
    if (MediaQuery.sizeOf(context).width < 900) {
      // 窄屏与 BentoLayout 同一条判据：改成上下堆叠，不再互相约束高度。
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [follower, const SizedBox(height: 12), anchor],
      );
    }
    return Row(
      // 锚那一栏不加约束：它一旦被压成固定高度，就再也量不出自己的真实高度了。
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          flex: 62,
          child: SizedBox(height: _height, child: follower),
        ),
        const SizedBox(width: 12),
        Expanded(flex: 38, child: anchor),
      ],
    );
  }
}

/// 读模块自带的 README 并弹窗预览。
Future<void> _showReadme(
  BuildContext context,
  StudioEngine engine,
  ModuleInfo module,
) async {
  final t = AppScope.of(context);
  final palette = AppScope.colors(context);
  final readme = await engine.readReadme(module.id) ?? const <String, Object?>{};
  if (!context.mounted) return;
  await showDialog<void>(
    context: context,
    // Overlay 里的那一层拿不到页面的取值环境，对话框要自己带一份令牌。
    builder: (_) => AppScope(
      tokens: t,
      palette: palette,
      child: _ReadmeDialog(module: module, readme: readme),
    ),
  );
}

class _ReadmeDialog extends StatelessWidget {
  const _ReadmeDialog({required this.module, required this.readme});

  final ModuleInfo module;
  final Map<String, Object?> readme;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final text = '${readme['text'] ?? ''}';
    final path = '${readme['path'] ?? ''}';
    final missing = readme['missing'] == true || text.isEmpty;
    return Dialog(
      backgroundColor: t.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusCard),
        side: BorderSide(color: t.edge),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 620, maxHeight: 460),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${module.name} · README',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        height: 1,
                        fontWeight: FontWeight.w600,
                        color: t.textPrimary,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  TinyLabel(
                    missing ? 'modules/${module.id}/' : path,
                    color: t.textTertiary,
                    size: 9,
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Expanded(
                child: missing
                    ? Center(
                        child: Text(
                          '这个模块没有随包携带 README。\n'
                          '把说明文档放到 modules/${module.id}/README.md 就会在这里显示。',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            fontSize: 11,
                            height: 1.7,
                            color: t.textSecondary,
                          ),
                        ),
                      )
                    : SingleChildScrollView(
                        child: SelectableText(
                          readme['truncated'] == true
                              ? '$text\n\n（正文过长，只截到前 200 KB）'
                              : text,
                          style: TextStyle(
                            fontFamily: 'Consolas',
                            fontSize: 10.5,
                            height: 1.6,
                            color: t.textSecondary,
                          ),
                        ),
                      ),
              ),
              const SizedBox(height: 12),
              Align(
                alignment: Alignment.centerRight,
                child: GhostButton(
                  label: '关闭',
                  dense: true,
                  onTap: () => Navigator.of(context).maybePop(),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
