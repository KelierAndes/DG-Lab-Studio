import 'package:flutter/material.dart' hide Stack;
import 'package:flutter/services.dart';

import '../app/field_metrics.dart';
import '../app/tokens.dart';
import 'flow_models.dart';

/// 建卡搜索面板：右键空白处、或把线拉到空白处时弹出，边打字边过滤。
///
/// 拉线进来时按引脚类型过滤（只显示接得上的卡），选中即建卡并自动接线；
/// 键盘 ↑↓ / PgUp PgDn / Enter / Esc 与旧界面一致。
class CardPalette extends StatefulWidget {
  const CardPalette({
    super.key,
    required this.defs,
    required this.onPick,
    required this.onClose,
    this.filterHint,
    this.width = 324,
    this.maxHeight = 460,
  });

  final List<FlowDef> defs;
  final void Function(FlowDef def) onPick;
  final VoidCallback onClose;

  /// 例如「仅显示可连接「数值」的卡片」；为空表示不过滤。
  final String? filterHint;
  final double width;

  /// 弹出点离画布下边太近时面板就地变矮，而不是整体往上跳。
  final double maxHeight;

  @override
  State<CardPalette> createState() => _CardPaletteState();
}

class _CardPaletteState extends State<CardPalette> {
  final TextEditingController _query = TextEditingController();
  final FocusNode _focus = FocusNode();
  List<FlowDef> _rows = const [];
  int _index = 0;

  // 分类顺序照旧界面：事件 → 流程控制 → 核心变量 → 变量 → 运算 → 常量。
  static const _groupOrder = [
    '模块事件',
    '周期',
    '流程控制',
    '核心写入',
    '核心读出',
    '变量',
    '临时变量',
    '数学',
    '逻辑',
    '表达式',
    '范围',
    '实用',
    '常数',
  ];

  @override
  void initState() {
    super.initState();
    _refilter();
    // 右键一开就能直接打字（与旧界面一致）：不抢焦点的话，敲进去的字符会落到
    // 画布上，面板看起来像坏了——列表一直不过滤。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _focus.requestFocus();
    });
  }

  @override
  void dispose() {
    _query.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _refilter() {
    final q = _query.text.trim().toLowerCase();
    final scored = <(int, FlowDef)>[];
    for (final d in widget.defs) {
      var score = 0;
      if (q.isEmpty) {
        score = 1;
      } else {
        if (d.title.toLowerCase().contains(q)) score = 90;
        if (d.en.toLowerCase().contains(q)) score = score > 0 ? score + 8 : 80;
        if (d.key.toLowerCase().contains(q)) score = score > 0 ? score + 4 : 55;
        if (score == 0 && d.category.contains(q)) score = 30;
        if (score == 0 && d.tip.toLowerCase().contains(q)) score = 20;
      }
      if (score > 0) scored.add((score, d));
    }
    int groupOf(FlowDef d) {
      final i = _groupOrder.indexOf(d.category);
      return i < 0 ? _groupOrder.length : i;
    }

    scored.sort(
      (a, b) =>
          a.$2.title.compareTo(b.$2.title) != 0 &&
              groupOf(a.$2) == groupOf(b.$2)
          ? a.$2.title.compareTo(b.$2.title)
          : (groupOf(a.$2) != groupOf(b.$2)
                ? groupOf(a.$2).compareTo(groupOf(b.$2))
                : b.$1.compareTo(a.$1)),
    );
    setState(() {
      _rows = [for (final s in scored) s.$2];
      _index = _rows.isEmpty ? 0 : _index.clamp(0, _rows.length - 1);
    });
  }

  void _move(int delta) {
    if (_rows.isEmpty) return;
    setState(() => _index = (_index + delta).clamp(0, _rows.length - 1));
  }

  void _pickAt(int i) {
    if (i < 0 || i >= _rows.length) return;
    widget.onPick(_rows[i]);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    // 分组标题只在没在打字时显示：打字时按分数排，分组反而看不懂。
    final grouped = <int, int>{};
    if (_query.text.trim().isEmpty) {
      var last = '';
      for (var i = 0; i < _rows.length; i++) {
        if (_rows[i].category != last) {
          grouped[i] = _rows
              .where((d) => d.category == _rows[i].category)
              .length;
          last = _rows[i].category;
        }
      }
    }
    return Focus(
      autofocus: true,
      onKeyEvent: (node, event) {
        if (event is! KeyDownEvent && event is! KeyRepeatEvent) {
          return KeyEventResult.ignored;
        }
        final key = event.logicalKey;
        if (key == LogicalKeyboardKey.escape) {
          widget.onClose();
          return KeyEventResult.handled;
        }
        if (key == LogicalKeyboardKey.arrowDown) {
          _move(1);
          return KeyEventResult.handled;
        }
        if (key == LogicalKeyboardKey.arrowUp) {
          _move(-1);
          return KeyEventResult.handled;
        }
        if (key == LogicalKeyboardKey.pageDown) {
          _move(6);
          return KeyEventResult.handled;
        }
        if (key == LogicalKeyboardKey.pageUp) {
          _move(-6);
          return KeyEventResult.handled;
        }
        if (key == LogicalKeyboardKey.enter ||
            key == LogicalKeyboardKey.numpadEnter ||
            key == LogicalKeyboardKey.tab) {
          _pickAt(_index);
          return KeyEventResult.handled;
        }
        return KeyEventResult.ignored;
      },
      child: Container(
        width: widget.width,
        constraints: BoxConstraints(maxHeight: widget.maxHeight),
        decoration: BoxDecoration(
          color: t.surface,
          borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          border: Border.all(color: t.edge),
          boxShadow: t.raised(depth: 1.4),
        ),
        padding: const EdgeInsets.fromLTRB(10, 10, 10, 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            singleLineBox(
              TextField(
                key: const ValueKey('palette-search'),
                controller: _query,
                focusNode: _focus,
                maxLines: 1,
                textAlignVertical: TextAlignVertical.center, // 与其它输入框同一基线
                onChanged: (_) => _refilter(),
                style: fieldTextStyle(t.textPrimary),
                strutStyle: fieldStrut(fontSize: 11.5),
                cursorColor: t.accent,
                decoration: InputDecoration(
                  isDense: true,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 8,
                  ),
                  filled: true,
                  fillColor: t.wellDeep,
                  hintText: '搜索卡片：中文 / 英文 / 拼音',
                  hintStyle: fieldHintStyle(t.textTertiary),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(AppTokens.radiusField),
                    borderSide: BorderSide(color: t.edge),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(AppTokens.radiusField),
                    borderSide: BorderSide(color: t.edge),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(AppTokens.radiusField),
                    borderSide: BorderSide(color: t.accent.withAlpha(160)),
                  ),
                ),
              ),
            ),
            if (widget.filterHint case final hint?) ...[
              const SizedBox(height: 6),
              Text(
                hint,
                style: TextStyle(
                  fontSize: 9,
                  height: 1.3,
                  color: t.textTertiary,
                ),
              ),
            ],
            const SizedBox(height: 6),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                padding: EdgeInsets.zero,
                children: [
                  for (var i = 0; i < _rows.length; i++) ...[
                    if (grouped.containsKey(i))
                      Padding(
                        padding: const EdgeInsets.fromLTRB(2, 6, 2, 3),
                        child: Text(
                          '${_rows[i].category} · ${grouped[i]}',
                          style: TextStyle(
                            fontSize: 9,
                            letterSpacing: .6,
                            height: 1,
                            fontWeight: FontWeight.w600,
                            color: t.textTertiary,
                          ),
                        ),
                      ),
                    MouseRegion(
                      key: ValueKey('palette-row-${_rows[i].key}'),
                      cursor: SystemMouseCursors.click,
                      child: GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onTap: () => _pickAt(i),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 6,
                            vertical: 5,
                          ),
                          decoration: BoxDecoration(
                            color: i == _index ? t.accent.withAlpha(34) : null,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Row(
                            children: [
                              Container(
                                width: 3,
                                height: 14,
                                decoration: BoxDecoration(
                                  color: i == _index ? t.accent : t.edge,
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                              const SizedBox(width: 7),
                              Expanded(
                                child: Text(
                                  _rows[i].title,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    fontSize: 11.5,
                                    height: 1.2,
                                    color: i == _index
                                        ? t.textPrimary
                                        : t.textSecondary,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 6),
                              Text(
                                _rows[i].en.isEmpty
                                    ? _rows[i].key.split('.').last
                                    : _rows[i].en,
                                maxLines: 1,
                                style: TextStyle(
                                  fontFamily: 'Consolas',
                                  fontSize: 9,
                                  height: 1.2,
                                  color: t.textTertiary,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ],
                  if (_rows.isEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(4, 8, 4, 8),
                      child: Text(
                        '没有匹配的卡片',
                        style: TextStyle(
                          fontSize: 10.5,
                          height: 1.3,
                          color: t.textTertiary,
                        ),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 4),
            Text(
              '↑↓ 选择 · Enter 创建 · 单击创建 · Esc 关闭',
              style: TextStyle(
                fontSize: 8.5,
                height: 1.2,
                color: t.textTertiary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
