import 'dart:async';

import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/services.dart';

import '../app/tokens.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../chrome/profile_kit.dart';
import '../engine/engine_state.dart';
import 'binding_pad.dart';

/// 负鼠按键映射：11 个按键位各自绑一个动作，动作目录由引擎给（内置 + 键盘 + 模块）。
///
/// 一份配置一个 JSON，改一个键也要整包写回，所以这里只发「某一位改成什么」，
/// 落盘由桥那侧整包完成；配置档的新建 / 改名 / 切换同样走引擎。
class OvcBindingCard extends StatefulWidget {
  const OvcBindingCard({
    super.key,
    required this.engine,
    required this.slotLabel,
  });

  final StudioEngine engine;
  final String slotLabel;

  @override
  State<OvcBindingCard> createState() => _OvcBindingCardState();
}

class _OvcBindingCardState extends State<OvcBindingCard> {
  Map<String, Object?> _data = const {};
  bool _loading = false;
  String? _error;

  List<Map<String, Object?>> get _buttons => [
    for (final b in (_data['buttons'] as List? ?? const []))
      Map<String, Object?>.from(b as Map),
  ];

  List<Map<String, Object?>> get _actions => [
    for (final a in (_data['actions'] as List? ?? const []))
      Map<String, Object?>.from(a as Map),
  ];

  List<String> get _keys => [
    for (final k in (_data['keys'] as List? ?? const [])) '$k',
  ];

  Map<String, Object?> get _bindings =>
      Map<String, Object?>.from(_data['bindings'] as Map? ?? const {});

  List<String> get _profiles => [
    for (final n in (_data['order'] as List? ?? const [])) '$n',
  ];

  String get _active => '${_data['active'] ?? '默认'}';

  Map<String, Object?> get _missing =>
      Map<String, Object?>.from(_data['missing'] as Map? ?? const {});

  @override
  void initState() {
    super.initState();
    unawaited(_load());
    widget.engine.addListener(_onEnginePush);
  }

  @override
  void dispose() {
    widget.engine.removeListener(_onEnginePush);
    super.dispose();
  }

  /// 模块停用把注入的那份按键映射收回去时，配置档下拉要跟着重拉。
  int _profilesRev = 0;

  void _onEnginePush() {
    if (widget.engine.profilesRev == _profilesRev) return;
    _profilesRev = widget.engine.profilesRev;
    if (mounted) unawaited(_load());
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final result = await widget.engine.invoke('bindings.get');
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (result is Map) _data = Map<String, Object?>.from(result);
    });
  }

  Future<void> _send(
    String method, [
    Map<String, Object?> args = const {},
  ]) async {
    final result = await widget.engine.invoke(method, args);
    if (!mounted || result is! Map) return;
    final payload = Map<String, Object?>.from(result);
    setState(() {
      _data = payload;
      _error = '${payload['error'] ?? ''}'.isEmpty
          ? null
          : '${payload['error']}';
    });
  }

  String _labelFor(String action) {
    if (action.isEmpty || action == 'none') return '无';
    final key = action.split(':').first;
    final argument = action.contains(':')
        ? action.substring(action.indexOf(':') + 1)
        : '';
    for (final a in _actions) {
      if (a['key'] == key) {
        return argument.isEmpty ? '${a['label']}' : '${a['label']} · $argument';
      }
    }
    return action;
  }

  Future<void> _pickAction(String bit, String current) async {
    // 对话框挂在 Navigator 的 Overlay 里，那里看不到 AppScope：令牌要先取好，
    // 再把整棵对话框包回一个 AppScope——里面的输入框自己也用 AppScope.of。
    final tokens = AppScope.of(context);
    final palette = AppScope.colors(context);
    final picked = await showDialog<String>(
      context: context,
      builder: (context) => AppScope(
        tokens: tokens,
        palette: palette,
        child: _ActionDialog(
          tokens: tokens,
          actions: _actions,
          keys: _keys,
          current: current,
        ),
      ),
    );
    if (picked == null) return;
    await _send('bindings.set', {'bit': bit, 'action': picked});
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AnimatedBuilder(
      animation: widget.engine,
      builder: (context, _) {
        return BentoCard(
          fit: true,
          title: '负鼠按键映射',
          accent: t.portModel,
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              ProfileKit(
                key: const ValueKey('binding-profile'),
                width: 110,
                unit: '映射',
                active: _active,
                options: _profiles,
                onSwitch: (name) => unawaited(
                  _send('bindings.profile_switch', {'name': name}),
                ),
                onCreate: () => unawaited(_send('bindings.profile_new', const {})),
                onRename: (from, to) => unawaited(
                  _send('bindings.profile_rename', {'name': from, 'new': to}),
                ),
                onDelete: () => unawaited(
                  _send('bindings.profile_remove', {'name': _active}),
                ),
              ),
              const SizedBox(width: 4),
              IconSquare(
                key: const ValueKey('binding-reload'),
                icon: Icons.refresh_rounded,
                size: 24,
                iconSize: 13,
                tooltip: '重新读取',
                onTap: () => unawaited(_load()),
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(10, 4, 10, 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                TinyLabel(
                  '${widget.slotLabel} · 按住即触发；HOME 连点 5 下可被插槽搜索连接',
                  color: t.textTertiary,
                  size: 9,
                ),
                const SizedBox(height: 4),
                if (_error case final why?)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: TinyLabel(why, color: t.portNegative),
                  ),
                // 可视化绑定：机身 + 引线 + 11 个按键按钮，按下时对应那颗键会亮。
                BindingPad(
                  buttons: _buttons,
                  pressed: {
                    for (final b in _buttons)
                      if (widget.engine.pressed(
                        int.tryParse('${b['bit']}') ?? -99,
                      ))
                        int.tryParse('${b['bit']}') ?? -99,
                  },
                  warning: {
                    for (final b in _buttons)
                      if (_missing.containsKey('${b['bit']}'))
                        int.tryParse('${b['bit']}') ?? -99,
                  },
                  labelOf: (bit) => _labelFor('${_bindings[bit] ?? 'none'}'),
                  onTapBit: (bit) => unawaited(
                    _pickAction(bit, '${_bindings[bit] ?? 'none'}'),
                  ),
                ),
                if (_buttons.isEmpty)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(2, 8, 2, 4),
                    child: TinyLabel(
                      _loading ? '正在读取映射…' : '没有读到按键位，先连上负鼠。',
                      color: t.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// 动作选择：内置 / 键盘 / 模块动作一列。
///
/// 两类动作要再问一步才绑得完整：「模拟键盘按键」要拿到按下的那颗键，
/// OSC 之类的模块动作要拿到参数（`osc:/avatar/parameters/...`）。旧界面就是
/// 一个输入框：点一下然后按键，或直接填路径；这里同一套语义，值形如 `<key>:<参数>`。
class _ActionDialog extends StatefulWidget {
  const _ActionDialog({
    required this.tokens,
    required this.actions,
    required this.keys,
    required this.current,
  });

  /// 对话框挂在 Navigator 的 Overlay 里，看不到外壳的 AppScope，所以令牌由调用方给。
  final AppTokens tokens;
  final List<Map<String, Object?>> actions;
  final List<String> keys;
  final String current;

  @override
  State<_ActionDialog> createState() => _ActionDialogState();
}

class _ActionDialogState extends State<_ActionDialog> {
  /// list → arg（要参数）→ capture（按键盘）。
  _DialogStep _step = _DialogStep.list;
  Map<String, Object?>? _pending;
  String _argValue = '';
  String? _captured;

  void _choose(Map<String, Object?> a) {
    final key = '${a['key']}';
    if (a['argument'] case final hint? when '$hint'.isNotEmpty) {
      setState(() {
        _step = key == 'key' ? _DialogStep.capture : _DialogStep.arg;
        _pending = a;
        _argValue = widget.current.startsWith('$key:')
            ? widget.current.substring(key.length + 1)
            : '';
        _captured = null;
      });
      return;
    }
    Navigator.of(context).pop(key);
  }

  void _submit() {
    final key = '${_pending?['key'] ?? ''}';
    if (key.isEmpty) return;
    final value = _step == _DialogStep.capture ? _captured : _argValue.trim();
    if (value == null || value.isEmpty) {
      setState(() => _captured ??= '还没按下按键');
      return;
    }
    Navigator.of(context).pop('$key:$value');
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent && event is! KeyRepeatEvent) {
      return KeyEventResult.ignored;
    }
    if (event.logicalKey == LogicalKeyboardKey.escape) {
      setState(() => _step = _DialogStep.list);
      return KeyEventResult.handled;
    }
    if (event.logicalKey == LogicalKeyboardKey.enter ||
        event.logicalKey == LogicalKeyboardKey.numpadEnter) {
      _submit();
      return KeyEventResult.handled;
    }
    // keyLabel 给可打印字符（'a' / '2' / ','），功能键没有 label 就从 debugName 里剥。
    final raw = event.logicalKey.keyLabel.trim().isEmpty
        ? event.logicalKey.debugName?.split(' ').last ?? ''
        : event.logicalKey.keyLabel.trim();
    final name = raw.toUpperCase();
    // 键名以引擎那张表为准（注入时按它查 VK 码），表里没有也照写，让引擎去兜底。
    final hit = widget.keys.firstWhere(
      (k) => k.toUpperCase() == name || k.toUpperCase() == 'KEY_$name',
      orElse: () => '',
    );
    setState(() => _captured = hit.isEmpty ? name : hit);
    return KeyEventResult.handled;
  }

  /// 动作分两组列。设备动作就有十几条，键盘注入与随模块注册的「发送 OSC」
  /// 要是排在尾巴上，翻不到就等于没搬过来。
  Widget _actionList(AppTokens t) {
    final rows = <Widget>[
      for (final builtin in [true, false]) ...[
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 8, 4, 2),
          child: Text(
            builtin ? '设备动作' : '键盘与模块动作',
            style: TextStyle(
              fontSize: 9.5,
              letterSpacing: .8,
              fontWeight: FontWeight.w600,
              color: t.textTertiary,
            ),
          ),
        ),
        for (final a in widget.actions.where(
          (a) => (a['builtin'] ?? true) == builtin,
        ))
          ListTile(
            dense: true,
            onTap: () => _choose(a),
            title: Text(
              '${a['label']}',
              style: TextStyle(fontSize: 11.5, color: t.textSecondary),
            ),
            subtitle: '${a['argument'] ?? ''}'.isEmpty
                ? null
                : Text(
                    '需要参数：${a['argument']}',
                    style: TextStyle(fontSize: 9, color: t.textTertiary),
                  ),
            trailing: widget.current.split(':').first == a['key']
                ? Icon(Icons.check_rounded, size: 15, color: t.accent)
                : null,
          ),
      ],
    ];
    return Scrollbar(
      child: ListView(padding: EdgeInsets.zero, children: rows),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.tokens;
    final title = switch (_step) {
      _DialogStep.list => '绑到什么动作',
      _DialogStep.arg => '${_pending?['label'] ?? '动作'} · 要发什么参数',
      _DialogStep.capture => '要模拟哪个按键',
    };
    return AlertDialog(
      backgroundColor: t.surface,
      title: Text(
        title,
        style: TextStyle(fontSize: 13, color: t.textSecondary),
      ),
      content: SizedBox(
        width: 340,
        height: 420,
        child: switch (_step) {
          _DialogStep.list => _actionList(t),
          _ => Padding(
            padding: const EdgeInsets.fromLTRB(2, 4, 2, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (_step == _DialogStep.arg)
                  CommitField(
                    key: const ValueKey('binding-arg'),
                    text: _argValue,
                    hint: '${_pending?['argument'] ?? '参数'}',
                    minWidth: 320,
                    onSubmit: (v) {
                      _argValue = v;
                      _submit();
                    },
                  ),
                if (_step == _DialogStep.capture)
                  Focus(
                    autofocus: true,
                    onKeyEvent: _onKey,
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 14,
                      ),
                      decoration: BoxDecoration(
                        color: t.wellDeep,
                        borderRadius: BorderRadius.circular(
                          AppTokens.radiusField,
                        ),
                        border: Border.all(color: t.edge),
                      ),
                      child: Text(
                        _captured ?? '点击此处后按下要绑定的按键',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontFamily: 'Consolas',
                          fontSize: 12,
                          color: _captured == null
                              ? t.textTertiary
                              : t.textPrimary,
                        ),
                      ),
                    ),
                  ),
                const SizedBox(height: 10),
                Text(
                  _step == _DialogStep.capture
                      ? '按 Esc 返回动作列表，按 Enter 确认'
                      : '留空则不改动；按 Enter 确认',
                  style: TextStyle(fontSize: 9.5, color: t.textTertiary),
                ),
                if (_captured case final k? when _step == _DialogStep.capture)
                  Text(
                    '已捕获：$k',
                    style: TextStyle(
                      fontFamily: 'Consolas',
                      fontSize: 10.5,
                      color: t.accent,
                    ),
                  ),
              ],
            ),
          ),
        },
      ),
      actions: [
        if (_step != _DialogStep.list)
          TextButton(
            onPressed: () => setState(() => _step = _DialogStep.list),
            child: Text(
              '返回',
              style: TextStyle(fontSize: 11.5, color: t.textTertiary),
            ),
          ),
        if (_step != _DialogStep.list)
          TextButton(
            onPressed: _submit,
            child: Text(
              '确定',
              style: TextStyle(fontSize: 11.5, color: t.accent),
            ),
          ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(
            '取消',
            style: TextStyle(fontSize: 11.5, color: t.textTertiary),
          ),
        ),
      ],
    );
  }
}

enum _DialogStep { list, arg, capture }
