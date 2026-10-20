import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:window_manager/window_manager.dart';

import '../chrome/nav_rail.dart';
import '../chrome/scroll_guard.dart';
import '../chrome/device_icons.dart';
import '../chrome/module_icons.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../chrome/profile_kit.dart';
import '../chrome/top_bar.dart';
import '../engine/engine_state.dart';
import '../flow/card_palette.dart';
import '../flow/flow_editor.dart';
import '../flow/flow_models.dart';
import '../flow/vars_panel.dart';
import '../graph/graph_canvas.dart';
import '../graph/graph_model.dart';
import '../pages/actions.dart';
import '../pages/app_pages.dart';
import '../pages/connect_page.dart';
import '../pages/control_page.dart';
import '../pages/dashboard_page.dart';
import '../pages/log_page.dart';
import '../pages/modules_page.dart';
import '../pages/settings_page.dart';
import 'studio_prefs.dart';
import 'tokens.dart';
import 'window_resize.dart';

class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.engine});

  final StudioEngine engine;

  @override
  State<AppShell> createState() => AppShellState();
}

class AppShellState extends State<AppShell> with WidgetsBindingObserver {
  final PopoverService _popover = PopoverService();
  final StudioPrefs _prefs = StudioPrefs();
  final AppNav _nav = AppNav();

  /// 事件流页的画布。外壳持有它，页面只通过 [StudioActions] 影响它。
  final GraphController _graph = GraphController(
    nodes: [],
    edges: [],
    title: '事件流',
    // 异类型不再在画布上被拒：落线后引擎在中间补一张转换卡。
    autoConvert: true,
  );

  /// 画布内容与引擎的往返都归这个编辑器；外壳只负责把它挂到页面上。
  late final FlowEditor _flow = FlowEditor(
    engine: widget.engine,
    graph: _graph,
    onLayout: () {
      // fitWorkflow 只框图片工作流那几类卡片，事件流要按整张图适配。
      if (_graph.viewport.width > 0) {
        _graph.fitView(_graph.viewport, padding: 26, leftInset: _chromeWidth);
      }
    },
  );

  late final StudioActions _actions = StudioActions(
    invoke: widget.engine.invoke,
    toast: _showToast,
    selectSlot: (id) async {
      _nav.selectSlot(id);
      await widget.engine.invoke('engine.select_slot', {'slot_id': id});
    },
    spawnType: _spawnType,
  );

  final FocusNode _focus = FocusNode(
    canRequestFocus: true,
    skipTraversal: false,
  );

  bool _showConsole = false;
  _PaletteAsk? _palette;
  int _flowTab = 0;
  String? _toast;
  Timer? _toastTimer;
  DateTime? _toastAt;

  AppTokens get tokens => _prefs.lightTheme ? AppTokens.light : AppTokens.dark;
  GraphController get graph => _graph;
  AppNav get nav => _nav;
  StudioPrefs get prefs => _prefs;
  StudioEngine get engine => widget.engine;

  /// 抽屉浮在内容之上，画布与页面都用这条宽度避让。
  double get _chromeWidth => StudioChrome.chromeWidth(_prefs);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // 外观开关写回 config.json 的 ui.*，换机器也不用重设一遍。
    _prefs.persist = (key, value) => unawaited(
      widget.engine.invoke('settings.set', {'key': key, 'value': value}),
    );
    _merged.addListener(_onChanged);
    // 拉线落到空白处：打开按引脚类型过滤的建卡搜索，选中即建卡并接线。
    _graph.onLinkMiss = (origin, dir, world) {
      final type = _flow.pinType(origin, output: dir == PortDir.output);
      setState(
        () => _palette = _PaletteAsk(
          world: world,
          view: _graph.toView(world),
          origin: origin,
          dir: dir,
          type: type,
        ),
      );
    };
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focus.requestFocus();
      if (mounted && _graph.viewport.width > 0) {
        _graph.fitWorkflow(_graph.viewport, leftInset: _chromeWidth);
      }
      unawaited(widget.engine.start());
    });
    // 启动时查一次更新：等引擎连上、config 到了再查，有新版只提示不安装。
    _updateTimer = Timer(const Duration(seconds: 8), _autoCheckUpdate);
  }

  Timer? _updateTimer;

  Future<void> _autoCheckUpdate() async {
    final engine = widget.engine;
    if (!engine.online) return;
    final settings = engine.config['settings'];
    final auto = settings is Map ? settings['update.auto'] : null;
    if (auto == false) return;
    await engine.checkUpdate();
    final info = engine.updateInfo;
    if (info != null && info['has_update'] == true) {
      engine.say('发现新版本 v${info['latest']}，可在「设置 → 软件更新」升级');
    }
  }

  @override
  void dispose() {
    _toastTimer?.cancel();
    _updateTimer?.cancel();
    _merged.removeListener(_onChanged);
    _flow.dispose();
    WidgetsBinding.instance.removeObserver(this);
    _popover.dispose();
    _focus.dispose();
    super.dispose();
  }

  late final Listenable _merged = Listenable.merge([
    _graph,
    _prefs,
    _nav,
    _flow,
    widget.engine,
  ]);

  /// 抽屉的内容跟着当前页与事件流状态走，变量表的行数也会变。
  late final Listenable _drawerChanged = Listenable.merge([_nav, _flow]);

  /// 画布、引擎和外壳自己都会抛出一次性提示，谁最后说话听谁的。
  void _onChanged() {
    if (!mounted) return;
    final settings = widget.engine.config['settings'];
    if (settings is Map) {
      _prefs.loadFromEngine(Map<String, Object?>.from(settings));
    }
    final candidates = <(String?, DateTime?)>[
      (_graph.status, _graph.statusAt),
      (_prefs.status, _prefs.statusAt),
      (widget.engine.status, widget.engine.statusAt),
    ];
    (String?, DateTime?)? winner;
    for (final c in candidates) {
      if (c.$1 == null) continue;
      if (winner == null ||
          (c.$2 ?? DateTime(2000)).isAfter(winner.$2 ?? DateTime(2000))) {
        winner = c;
      }
    }
    if (winner != null && winner.$1 != _toast && winner.$2 != _toastAt) {
      _toastAt = winner.$2;
      _toast = winner.$1;
      _toastTimer?.cancel();
      _toastTimer = Timer(const Duration(milliseconds: 1800), () {
        if (mounted) setState(() => _toast = null);
      });
    }
    setState(() {});
  }

  void _showToast(String message) {
    _toastAt = DateTime.now();
    _toast = message;
    _toastTimer?.cancel();
    _toastTimer = Timer(const Duration(milliseconds: 1800), () {
      if (mounted) setState(() => _toast = null);
    });
    setState(() {});
  }

  /// 抽屉与「放置」都以此为落点：抽屉浮在画布上，所以取没被盖住的那条中心。
  Offset get _viewportCenter {
    final left = math.min(_chromeWidth, _graph.viewport.width / 2);
    return Offset(
      left + (_graph.viewport.width - left) / 2,
      _graph.viewport.height / 2,
    );
  }

  /// 画布上方的一条浮动工具条：配置、页签、整理与保存。
  Widget _flowToolbar() {
    return RaisedPill(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          ProfileKit(
            key: const ValueKey('flow-profile'),
            width: 132,
            active: _flow.profile,
            options: _flow.profiles,
            copyOnNew: true,
            onSwitch: (name) => unawaited(_flow.switchProfile(name)),
            onCreate: () => unawaited(_flow.newProfile()),
            onRename: (from, to) => unawaited(_flow.renameProfile(from, to)),
            onDelete: () => unawaited(_flow.removeProfile(_flow.profile)),
          ),
          const SizedBox(width: 8),
          SegmentedPills(
            key: const ValueKey('flow-pages'),
            options: const ['输入', '输出'],
            index: _flow.page == 'input' ? 0 : 1,
            compact: true,
            onPick: (i) =>
                unawaited(_flow.setPage(i == 0 ? 'input' : 'output')),
          ),
          const SizedBox(width: 8),
          GhostButton(
            key: const ValueKey('flow-arrange'),
            label: '整理',
            icon: Icons.auto_awesome_motion_rounded,
            dense: true,
            onTap: () => unawaited(_flow.arrange()),
          ),
          const SizedBox(width: 5),
          GhostButton(
            key: const ValueKey('flow-save'),
            label: '保存',
            icon: Icons.save_outlined,
            dense: true,
            onTap: () => unawaited(_flow.saveNow()),
          ),
          const SizedBox(width: 5),
          GhostButton(
            key: const ValueKey('flow-delete'),
            label: '删除选中',
            icon: Icons.delete_outline_rounded,
            dense: true,
            onTap: () => unawaited(_flow.deleteSelected()),
          ),
          const SizedBox(width: 5),
          GhostButton(
            key: const ValueKey('flow-clear'),
            label: _confirmClear ? '再点一次确认' : '清空本页',
            icon: Icons.layers_clear_outlined,
            dense: true,
            active: _confirmClear,
            onTap: _clearPage,
          ),
        ],
      ),
    );
  }

  void _spawnType(String type) {
    if (_nav.page != AppPage.flow) _nav.go(AppPage.flow);
    final world = _graph.toWorld(_viewportCenter);
    unawaited(_flow.addCard(type, world));
  }

  Widget _pageLayer() => switch (_nav.page) {
    AppPage.dashboard => DashboardPage(
      nav: _nav,
      engine: widget.engine,
      actions: _actions,
    ),
    AppPage.connect => ConnectPage(
      nav: _nav,
      engine: widget.engine,
      actions: _actions,
    ),
    AppPage.control => ControlPage(
      nav: _nav,
      engine: widget.engine,
      actions: _actions,
    ),
    AppPage.flow => const SizedBox.shrink(),

    AppPage.modules => ModulesPage(
      nav: _nav,
      engine: widget.engine,
      actions: _actions,
    ),
    AppPage.log => LogPage(nav: _nav, engine: widget.engine, actions: _actions),
    AppPage.settings => SettingsPage(
      nav: _nav,
      engine: widget.engine,
      prefs: _prefs,
      actions: _actions,
    ),
  };

  /// 抽屉跟着页面换内容：外壳管外观，列表归当前页。
  ({String title, int count, Widget body}) _drawer() {
    final slots = widget.engine.snapshot.slots;
    return switch (_nav.page) {
      AppPage.dashboard || AppPage.control => (
        title: '设备槽位',
        count: slots.length,
        body: ListView(
          padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
          children: [
            for (final s in slots)
              if (_nav.matches(s.label))
                DrawerRow(
                  key: ValueKey('drawer-slot-${s.slotId}'),
                  // 郊狼 / 负鼠 / 灵猫各有各的线条标记，一眼分清设备。
                  leading: DeviceGlyph(family: _familyOf(s.type), size: 17),
                  title: s.label,
                  subtitle: s.summary(),
                  selected:
                      s.slotId ==
                      (_nav.selectedSlot ?? widget.engine.snapshot.activeSlot),
                  onTap: () => _actions.selectSlot(s.slotId),
                  trailing: s.battery == null
                      ? null
                      : TinyLabel('${s.battery}%', color: tokens.textTertiary),
                ),
            if (slots.isEmpty) const EmptyNote('尚未发现设备，去「连接」页配对一台。'),
          ],
        ),
      ),
      AppPage.connect => (
        title: '接入通道',
        count: _channels.length,
        body: ListView(
          padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
          children: [
            for (final c in _channels)
              DrawerRow(
                key: ValueKey('drawer-backend-${c.$4}'),
                icon: c.$3,
                title: c.$1,
                subtitle: widget.engine.snapshot.channel(c.$4).connected
                    ? '${c.$2} · ${widget.engine.snapshot.channel(c.$4).statusText}'
                    : c.$2,
                titleTint: widget.engine.snapshot.channel(c.$4).connected
                    ? tokens.portPositive
                    : null,
                selected: widget.engine.snapshot.channel(c.$4).primary,
                onTap: () => _actions.invoke(c.$5),
              ),
          ],
        ),
      ),
      AppPage.flow => (
        title: _flowTab == 0 ? '卡片面板' : '变量表',
        count: _flowTab == 0
            ? _paletteDefs.length
            : _flow.systemVars.length + _flow.freeVars.length,
        body: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(8, 0, 8, 6),
              child: SegmentedPills(
                key: const ValueKey('flow-drawer-tabs'),
                options: const ['卡片面板', '变量表'],
                index: _flowTab,
                compact: true,
                onPick: (i) => setState(() => _flowTab = i),
              ),
            ),
            Expanded(
              child: _flowTab == 0
                  ? _paletteList()
                  : VarsPanel(
                      flow: _flow,
                      onSpawn: _spawnType,
                      matches: _nav.matches,
                    ),
            ),
          ],
        ),
      ),
      AppPage.modules => (
        title: '已安装模块',
        count: widget.engine.modules.length,
        body: ListView(
          padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
          children: [
            for (final m in widget.engine.modules)
              if (_nav.matches(m.name))
                DrawerRow(
                  key: ValueKey('drawer-module-${m.id}'),
                  icon: Icons.extension_rounded,
                  title: m.name,
                  subtitle: m.version,
                  selected: _nav.selectedModule == m.id,
                  // 已启用的模块名字点绿，一眼看出哪些在跑（与旧版列表一致）。
                  titleTint: m.enabled ? tokens.portPositive : null,
                  leading: ModuleGlyph(id: m.id, size: 17),
                  onTap: () => _nav.selectModule(m.id),
                  trailing: TinyLabel(
                    m.running ? '运行中' : (m.enabled ? '已启用' : '已停用'),
                    color: m.running ? tokens.accent : tokens.textTertiary,
                  ),
                ),
            if (widget.engine.modules.isEmpty) const EmptyNote('还没有安装模块。'),
          ],
        ),
      ),
      AppPage.log => (
        title: '日志筛选',
        count: widget.engine.logs.length,
        body: Padding(
          padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('级别'),
              const SizedBox(height: 6),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: SegmentedPills(
                  options: AppNav.logLevels,
                  index: _nav.logLevel,
                  compact: true,
                  onPick: _nav.setLogLevel,
                ),
              ),
              const SizedBox(height: 12),
              const SectionLabel('操作'),
              const SizedBox(height: 6),
              GhostButton(
                label: '清空日志',
                icon: Icons.delete_sweep_outlined,
                onTap: () {
                  widget.engine.clearLogs();
                  _showToast('日志已清空');
                },
              ),
            ],
          ),
        ),
      ),
      AppPage.settings => (
        title: '设置分组',
        count: 4,
        body: ListView(
          padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
          children: const [EmptyNote('设置项在右侧页面内分组。')],
        ),
      ),
    };
  }

  // 三条通道各一行；本地中继是通道自己的选项（在连接页卡片上、以及设置页），
  // 旧界面也没有把它当成第四种连接。
  static String _familyOf(String type) {
    final t = type.toUpperCase();
    if (t.startsWith('OVC')) return 'OVC';
    if (t.startsWith('BMTR')) return 'BMTR';
    return 'COYOTE';
  }

  static const _channels = <(String, String, IconData, String, String)>[
    ('Socket V4', '官方在线中继', Icons.link_rounded, 'v4', 'engine.connect_v4'),
    (
      'Socket V3',
      '旧版协议',
      Icons.wifi_tethering_rounded,
      'v3',
      'engine.connect_v3',
    ),
    ('蓝牙直连', 'BLE 扫描与配对', Icons.bluetooth_rounded, 'ble', 'engine.ble_scan'),
  ];

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent && event is! KeyRepeatEvent) {
      return KeyEventResult.ignored;
    }
    final key = event.logicalKey;
    if (key == LogicalKeyboardKey.escape) {
      if (_popover.isOpen) {
        _popover.close();
      } else if (_graph.pending != null) {
        _graph.cancelLink();
      } else if (_graph.selection.isNotEmpty || _graph.selectedEdge != null) {
        _graph.clear();
      } else {
        _prefs.toggleDrawer();
      }
      return KeyEventResult.handled;
    }
    if (_nav.page != AppPage.flow) return KeyEventResult.ignored;
    // 字段持有键盘时，Delete/Backspace 归字段。
    if (!_focus.hasPrimaryFocus) return KeyEventResult.ignored;
    if (key == LogicalKeyboardKey.delete ||
        key == LogicalKeyboardKey.backspace) {
      _graph.deleteSelection();
      return KeyEventResult.handled;
    }
    // 旧界面的那套快捷键照搬：Ctrl+C/V/X 卡片、Ctrl+S 保存、Tab 整理。
    if (HardwareKeyboard.instance.isControlPressed) {
      if (key == LogicalKeyboardKey.keyS) {
        unawaited(_flow.saveNow());
        return KeyEventResult.handled;
      }
      if (key == LogicalKeyboardKey.keyC) {
        _flow.copySelection();
        return KeyEventResult.handled;
      }
      if (key == LogicalKeyboardKey.keyV) {
        unawaited(_flow.paste());
        return KeyEventResult.handled;
      }
      if (key == LogicalKeyboardKey.keyX) {
        unawaited(_flow.cutSelection());
        return KeyEventResult.handled;
      }
      return KeyEventResult.ignored;
    }
    if (key == LogicalKeyboardKey.tab) {
      unawaited(_flow.arrange());
      return KeyEventResult.handled;
    }
    if (key == LogicalKeyboardKey.keyF) {
      _graph.fitView(_graph.viewport, leftInset: _chromeWidth);
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    // 显式的 Material 祖先：节点卡片里的输入框依赖它，外壳不能指望宿主提供。
    return Material(
      type: MaterialType.transparency,
      child: PopoverScope(
        service: _popover,
        child: AppScope(
          tokens: tokens,
          palette: const [],
          child: Focus(
            focusNode: _focus,
            autofocus: true,
            onKeyEvent: _onKey,
            child: LayoutBuilder(
              builder: (context, box) {
                final size = Size(box.maxWidth, box.maxHeight);
                final flow = _nav.page == AppPage.flow;
                if (flow && _flow.state.catalog.isEmpty && !_flow.busy) {
                  // 第一次进事件流页才向引擎要目录，切页不该重复拉。
                  WidgetsBinding.instance.addPostFrameCallback(
                    (_) => _flow.load(),
                  );
                }
                // 卡片上的实时值只有在这页才要，离开就停。
                if (flow) {
                  _flow.startLive();
                } else {
                  _flow.stopLive();
                }
                return Stack(
                  children: [
                    Positioned.fill(
                      child: Padding(
                        padding: const EdgeInsets.all(14),
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(
                              AppTokens.radiusPanel,
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: Colors.black.withAlpha(
                                  (tokens.shadowAlpha * 160).round(),
                                ),
                                blurRadius: 34,
                                offset: const Offset(0, 14),
                              ),
                            ],
                          ),
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(
                              AppTokens.radiusPanel,
                            ),
                            child: LayoutBuilder(
                              key: const Key('panel'),
                              builder: (context, inner) => Stack(
                                children: [
                                  Positioned.fill(
                                    child: DecoratedBox(
                                      decoration: BoxDecoration(
                                        gradient: LinearGradient(
                                          begin: Alignment.topCenter,
                                          end: Alignment.bottomCenter,
                                          colors: [
                                            tokens.canvasTop,
                                            tokens.canvasBottom,
                                          ],
                                        ),
                                      ),
                                    ),
                                  ),
                                  Positioned(
                                    left: 0,
                                    right: 0,
                                    top: AppTokens.topBarHeight,
                                    bottom: 0,
                                    child: flow
                                        ? _canvasLayer()
                                        : AnimatedBuilder(
                                            animation: _prefs,
                                            builder: (context, _) => Padding(
                                              // 抽屉展开时页面让出同样的宽度。
                                              padding: EdgeInsets.only(
                                                left: _prefs.drawerOpen
                                                    ? _chromeWidth
                                                    : 12,
                                              ),
                                              child: _pageLayer(),
                                            ),
                                          ),
                                  ),
                                  // 抽屉收起时留一个贴边把手（顶栏已经没有左栏图标位）。
                                  Positioned(
                                    left: 4,
                                    top: AppTokens.topBarHeight + 14,
                                    child: DrawerHandle(prefs: _prefs),
                                  ),
                                  Positioned(
                                    left: 12,
                                    top: AppTokens.topBarHeight + 2,
                                    bottom: 12,
                                    child: AnimatedBuilder(
                                      animation: _drawerChanged,
                                      builder: (context, _) {
                                        final d = _drawer();
                                        return StudioChrome(
                                          nav: _nav,
                                          prefs: _prefs,
                                          drawerTitle: d.title,
                                          drawerCount: d.count,
                                          // 抽屉里的列表自己滚到底时不该把页面一起拖走。
                                          drawerBody: IsolatedScroll(
                                            key: const ValueKey(
                                              'drawer-scroll',
                                            ),
                                            child: d.body,
                                          ),
                                        );
                                      },
                                    ),
                                  ),
                                  Positioned(
                                    left: 0,
                                    right: 0,
                                    top: 0,
                                    height: AppTokens.topBarHeight,
                                    child: WindowDragBar(
                                      child: TopBar(
                                        nav: _nav,
                                        engine: widget.engine,
                                        popover: _popover,
                                        onTheme: _prefs.toggleTheme,
                                      ),
                                    ),
                                  ),
                                  if (flow &&
                                      _showConsole &&
                                      _graph.log.isNotEmpty)
                                    Positioned(
                                      left: 18 + _chromeWidth,
                                      bottom: 86,
                                      child: OutputConsole(
                                        graph: _graph,
                                        onClose: () => setState(
                                          () => _showConsole = false,
                                        ),
                                      ),
                                    ),
                                  if (flow)
                                    Positioned(
                                      right: 18,
                                      bottom: 16,
                                      child: TelemetryBlock(
                                        engine: widget.engine,
                                      ),
                                    ),
                                  if (flow)
                                    Positioned(
                                      left: 18 + _chromeWidth,
                                      bottom: 16,
                                      // 只是给看的：画布上的拖框、平移都要照常收下事件。
                                      child: IgnorePointer(child: _FlowHints()),
                                    ),
                                  if (_toast != null)
                                    Positioned(
                                      left: 0,
                                      right: 0,
                                      // 事件流那页底部常驻快捷键条，提示胶囊让开一行。
                                      bottom: flow ? 46 : 16,
                                      child: Align(
                                        alignment: Alignment.bottomCenter,
                                        child: _Toast(text: _toast!),
                                      ),
                                    ),
                                ],
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                    PopoverLayer(service: _popover, boundary: size),
                    const WindowResizeHandles(),
                  ],
                );
              },
            ),
          ),
        ),
      ),
    );
  }

  /// 事件流页：画布占满内容区，抽屉浮在它左上方。
  bool _confirmClear = false;

  /// 旧界面要求双击确认，这里同样给一次反悔的机会。
  void _clearPage() {
    if (!_confirmClear) {
      setState(() => _confirmClear = true);
      Timer(const Duration(seconds: 4), () {
        if (mounted) setState(() => _confirmClear = false);
      });
      return;
    }
    _confirmClear = false;
    unawaited(_flow.clearPage());
  }

  /// 引擎目录里可放进面板的卡片，按分类分组、按当前页过滤。
  List<FlowDef> get _paletteDefs => [
    for (final d in _flow.state.catalog.values)
      if (d.palette && (_flow.page == 'input' ? d.onInputPage : d.onOutputPage))
        d,
  ];

  Widget _paletteList() => ListView(
    padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
    children: [
      for (final group in _paletteGroups) ...[
        SectionLabel(group.$1),
        for (final d in group.$2)
          if (_nav.matches(d.title) || _nav.matches(d.key))
            Draggable<String>(
              data: d.key,
              dragAnchorStrategy: pointerDragAnchorStrategy,
              feedback: const Padding(
                padding: EdgeInsets.all(18),
                child: Icon(Icons.add_box_outlined, size: 22),
              ),
              child: DrawerRow(
                key: ValueKey('palette-${d.key}'),
                icon: Icons.category_outlined,
                title: d.title,
                subtitle: d.tip,
                onTap: () => _spawnType(d.key),
              ),
            ),
      ],
      if (_paletteDefs.isEmpty) const EmptyNote('正在读取引擎卡片目录…'),
    ],
  );

  /// 拉线进来的那次请求要按引脚类型过滤；右键建卡不过滤。
  List<FlowDef> _paletteDefsFor(_PaletteAsk ask) {
    final all = _paletteDefs;
    if (ask.origin == null || ask.dir == null) return all;
    final receive = ask.dir == PortDir.output;
    return [
      for (final d in all)
        if (_flow.accepts(d, receive: receive, type: ask.type)) d,
    ];
  }

  static String _typeLabel(String type) => switch (type) {
    'exec' => '执行流',
    'int' => '整数',
    'bool' => '真假',
    'str' => '文本',
    'any' => '任意',
    _ => '数值',
  };

  List<(String, List<FlowDef>)> get _paletteGroups {
    final grouped = <String, List<FlowDef>>{};
    for (final d in _paletteDefs) {
      grouped
          .putIfAbsent(d.category.isEmpty ? '其它' : d.category, () => [])
          .add(d);
    }
    return [
      for (final e in grouped.entries)
        (e.key, e.value..sort((a, b) => a.title.compareTo(b.title))),
    ]..sort((a, b) => a.$1.compareTo(b.$1));
  }

  /// 建卡面板：左上角严格落在光标上。
  ///
  /// 原来是按固定尺寸先夹一次位置，光标靠近右下时整块面板往上/往左跳一大截，
  /// 看起来就是「弹菜和指针没关系」。现在改成让面板本身去适配光标处的剩余
  /// 空间：右边不够就变窄，下边不够就变矮，位置不动。
  Widget _paletteOverlay(_PaletteAsk ask) {
    final vw = _graph.viewport.width;
    final vh = _graph.viewport.height;
    final w = math.min(324.0, math.max(190.0, vw - ask.view.dx));
    final h = math.min(460.0, math.max(140.0, vh - ask.view.dy));
    return Positioned(
      left: ask.view.dx.clamp(0.0, math.max(0.0, vw - w)),
      top: ask.view.dy.clamp(0.0, math.max(0.0, vh - h)),
      child: CardPalette(
        width: w,
        maxHeight: h,
        defs: _paletteDefsFor(ask),
        filterHint: ask.origin == null
            ? null
            : '仅显示可接上「${_typeLabel(ask.type)}」的卡片 · 选中后自动接线',
        onPick: (d) {
          setState(() => _palette = null);
          if (ask.origin == null) {
            unawaited(_flow.addCard(d.key, ask.world));
          } else {
            unawaited(
              _flow.spawnAndLink(d, ask.origin!, ask.dir!, ask.type, ask.world),
            );
          }
        },
        onClose: () => setState(() => _palette = null),
      ),
    );
  }

  Widget _canvasLayer() {
    return Stack(
      children: [
        Positioned.fill(
          child: LayoutBuilder(
            key: const Key('canvas'),
            builder: (context, area) {
              _graph.viewport = area.biggest;
              final origin = (context.findRenderObject()! as RenderBox)
                  .localToGlobal(Offset.zero);
              final canvas = GraphCanvas(
                graph: _graph,
                popover: _popover,
                tokens: tokens,
                showGrid: _prefs.gridOn,
                flow: _flow,
                onAddCard: (world, view) => setState(
                  () => _palette = _PaletteAsk(world: world, view: view),
                ),
              );
              // 变量表与卡片面板可以拖到画布上建卡：落点就是卡片左上角。
              return DragTarget<String>(
                onAcceptWithDetails: (d) => unawaited(
                  _flow.addCard(d.data, _graph.toWorld(d.offset - origin)),
                ),
                builder: (context, _, _) => canvas,
              );
            },
          ),
        ),
        Positioned(left: 18 + _chromeWidth, top: 6, child: _flowToolbar()),
        if (_palette != null)
          Positioned.fill(
            // 点面板外面收起。以前是「搜索框一失焦就收起」，结果点面板里的卡片时先
            // 失焦：面板在按下那一刻就被摘掉，抬起时的 tap 永远等不到——用户看到的
            // 就是「菜单拉起来了，点了没反应，既不建卡也不接线」。
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapDown: (_) => setState(() => _palette = null),
            ),
          ),
        if (_palette case final ask?) _paletteOverlay(ask),
        Positioned(
          right: 16,
          top: 6,
          bottom: 6,
          child: Align(
            alignment: Alignment.centerRight,
            child: prefs.railOn
                ? RightRail(
                    graph: _graph,
                    leftInset: _chromeWidth,
                    gridOn: _prefs.gridOn,
                    onToggleGrid: _prefs.toggleGrid,
                    onRun: () => setState(() {
                      _showConsole = true;
                      _graph.run();
                    }),
                    onClearLog: () => setState(_graph.clearLog),
                  )
                : const SizedBox.shrink(),
          ),
        ),
      ],
    );
  }
}

/// 事件流画布底部的快捷键条：这一页的交互全在这几条上，写在界面上就不用翻文档。
class _FlowHints extends StatelessWidget {
  const _FlowHints();

  static const items = <(String, String)>[
    ('左键拖框', '框选'),
    ('右键拖框', '框选并删除'),
    ('中键拖动', '平移画布'),
    ('右键点线', '断开连线'),
    ('空白右键', '建卡搜索'),
    ('Delete', '删除选中'),
    ('Ctrl+C/V/X', '复制 · 粘贴 · 剪切'),
    ('Ctrl+S', '保存'),
    ('Tab', '整理布局'),
  ];

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return Container(
      constraints: const BoxConstraints(maxWidth: 780),
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
      decoration: BoxDecoration(
        color: t.surface.withAlpha(226),
        borderRadius: BorderRadius.circular(AppTokens.radiusPill),
        border: Border.all(color: t.hairline),
        boxShadow: t.raised(depth: .8),
      ),
      child: Wrap(
        spacing: 11,
        runSpacing: 5,
        children: [
          for (final (key, what) in items)
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  key,
                  style: TextStyle(
                    fontFamily: 'Consolas',
                    fontSize: 9.5,
                    height: 1,
                    fontWeight: FontWeight.w600,
                    color: t.textPrimary,
                  ),
                ),
                const SizedBox(width: 4),
                Text(
                  what,
                  style: TextStyle(
                    fontSize: 9.5,
                    height: 1,
                    color: t.textTertiary,
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _Toast extends StatelessWidget {
  const _Toast({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Color.alphaBlend(t.glassTint.withAlpha(120), t.surfaceRaised),
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: t.pillBorder),
        boxShadow: t.raised(depth: .8),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        child: Text(
          text,
          style: TextStyle(fontSize: 11.5, height: 1, color: t.textSecondary),
        ),
      ),
    );
  }
}

/// 顶栏那一条：拖拽移动窗口，双击空白处切换最大化。
///
/// 不用 `DragToMoveArea`：它会在整棵子树上注册双击识别器，栏内每个按钮都要
/// 等完双击时限才响应。这里把平移层做成半透明兄弟节点，只认领平移。
class WindowDragBar extends StatefulWidget {
  const WindowDragBar({super.key, required this.child});

  final Widget child;

  @override
  State<WindowDragBar> createState() => _WindowDragBarState();
}

class _WindowDragBarState extends State<WindowDragBar> {
  /// Windows 用 `GetDoubleClickTime()`（默认 500 毫秒）判定双击，而 Flutter 的
  /// 识别器固定 300 毫秒，正常速度的双击会漏掉。
  static const _clickInterval = Duration(milliseconds: 500);

  DateTime? _lastTap;

  Future<void> _toggleMaximise() async {
    if (await windowManager.isMaximized()) {
      await windowManager.unmaximize();
    } else {
      await windowManager.maximize();
    }
  }

  void _onTapUp(TapUpDetails _) {
    final now = DateTime.now();
    if (_lastTap != null && now.difference(_lastTap!) <= _clickInterval) {
      _lastTap = null;
      _toggleMaximise();
    } else {
      _lastTap = now;
    }
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.translucent,
      onPanStart: (_) => windowManager.startDragging(),
      child: Stack(
        children: [
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapUp: _onTapUp,
              child: const SizedBox.expand(),
            ),
          ),
          widget.child,
        ],
      ),
    );
  }
}

/// 一次建卡请求：世界坐标（卡片落点）+ 画布视图坐标（面板锚点）+
/// 拉线过来的起点（有它就按类型过滤并自动接线）。
class _PaletteAsk {
  const _PaletteAsk({
    required this.world,
    required this.view,
    this.origin,
    this.dir,
    this.type = 'any',
  });

  final Offset world;
  final Offset view;
  final PortRef? origin;
  final PortDir? dir;
  final String type;
}
