import 'package:flutter/material.dart';

import '../app/field_metrics.dart';
import '../app/tokens.dart';
import 'primitives.dart';

/// 配置档一整排：下拉选 + 新建 / 改名 / 删除。
///
/// 只有下拉的旧样子被当成「只能选」——用户要的是这一排能管这几份配置。
/// 改名与删除都作用在**当前这份**上：下拉里同时只有一个名字是可见的，
/// 叫用户去列表里挑一条再按删除，反而要猜删的是哪份。
class ProfileKit extends StatelessWidget {
  const ProfileKit({
    super.key,
    required this.active,
    required this.options,
    required this.onSwitch,
    required this.onCreate,
    required this.onRename,
    required this.onDelete,
    this.width = 120,
    this.copyOnNew = false,
    this.unit = '配置',
  });

  final String active;
  final List<String> options;
  final ValueChanged<String> onSwitch;
  final VoidCallback onCreate;
  final void Function(String from, String to) onRename;
  final VoidCallback onDelete;

  /// 新建出来的那份是复制当前这份（事件流）还是空白一份（按键映射）。
  final bool copyOnNew;

  /// 提示语里怎么称呼这一份：「配置」/「映射」。
  final String unit;

  final double width;

  bool get _canDelete => options.length > 1;

  Future<void> _rename(BuildContext context) async {
    // 对话框挂在 Navigator 的 Overlay 里，看不到外壳的 AppScope：令牌先取好再包回去。
    final tokens = AppScope.of(context);
    final palette = AppScope.colors(context);
    final to = await showDialog<String>(
      context: context,
      builder: (_) => AppScope(
        tokens: tokens,
        palette: palette,
        child: _NameDialog(
          title: '重命名$unit',
          initial: active,
          hint: '新的$unit名',
        ),
      ),
    );
    final next = (to ?? '').trim();
    if (next.isEmpty || next == active) return;
    onRename(active, next);
  }

  Future<void> _delete(BuildContext context) async {
    final tokens = AppScope.of(context);
    final palette = AppScope.colors(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AppScope(
        tokens: tokens,
        palette: palette,
        child: _ConfirmDialog(
          title: '删除$unit「$active」',
          body: _canDelete
              ? '删掉之后这一份就没了，其余$unit不受影响。'
              : '至少要保留一份$unit，这一份删不掉。',
          confirmLabel: '删除',
          enabled: _canDelete,
        ),
      ),
    );
    if (ok == true && _canDelete) onDelete();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final names = options.isEmpty ? const ['默认'] : options;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        PickerField(
          key: const ValueKey('profile-kit-pick'),
          width: width,
          compact: true,
          value: active,
          options: names,
          onChanged: onSwitch,
        ),
        const SizedBox(width: 4),
        IconSquare(
          key: const ValueKey('profile-kit-new'),
          icon: Icons.add_rounded,
          size: 24,
          iconSize: 13,
          tooltip: copyOnNew ? '复制当前这份，另开一份$unit' : '新建一份空白$unit',
          onTap: onCreate,
        ),
        const SizedBox(width: 4),
        IconSquare(
          key: const ValueKey('profile-kit-rename'),
          icon: Icons.drive_file_rename_outline_rounded,
          size: 24,
          iconSize: 13,
          tooltip: '重命名当前这份$unit「$active」',
          onTap: () async => _rename(context),
        ),
        const SizedBox(width: 4),
        IconSquare(
          key: const ValueKey('profile-kit-delete'),
          icon: Icons.remove_circle_outline_rounded,
          size: 24,
          iconSize: 13,
          tint: _canDelete ? t.portNegative : null,
          tooltip: _canDelete
              ? '删除当前这份$unit「$active」'
              : '至少要保留一份$unit',
          onTap: () async => _delete(context),
        ),
      ],
    );
  }
}

/// 一行名字输入框的对话框：回车即确定，按钮各管各的，不靠失焦落盘。
class _NameDialog extends StatefulWidget {
  const _NameDialog({
    required this.title,
    required this.initial,
    required this.hint,
  });

  final String title;
  final String initial;
  final String hint;

  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  late final TextEditingController _ctl = TextEditingController(
    text: widget.initial,
  );
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(_onFocus);
  }

  /// 一进来就把整份名字选中：改名是常态，逐字删掉旧名太费劲。
  void _onFocus() {
    if (!_focus.hasFocus || _ctl.text.isEmpty) return;
    _ctl.selection = TextSelection(baseOffset: 0, extentOffset: _ctl.text.length);
    _focus.removeListener(_onFocus);
  }

  @override
  void dispose() {
    _focus.removeListener(_onFocus);
    _focus.dispose();
    _ctl.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _ctl.text.trim();
    if (text.isEmpty) return;
    Navigator.of(context).pop(text);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AlertDialog(
      backgroundColor: t.surface,
      title: Text(
        widget.title,
        style: TextStyle(fontSize: 13, color: t.textSecondary),
      ),
      content: SizedBox(
        width: 260,
        height: fieldHeight,
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: t.wellDeep,
            borderRadius: BorderRadius.circular(AppTokens.radiusField),
            border: Border.all(
              color: _focus.hasFocus
                  ? t.edgeActive.withAlpha(120)
                  : Colors.black.withAlpha(110),
            ),
            boxShadow: t.inset,
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 7),
            child: singleLineBox(
              TextField(
                key: const ValueKey('profile-kit-name-field'),
                controller: _ctl,
                focusNode: _focus,
                autofocus: true,
                maxLines: 1,
                textAlignVertical: TextAlignVertical.center,
                style: fieldValueStyle.copyWith(color: t.textPrimary),
                strutStyle: fieldStrut(fontSize: 11.5),
                cursorColor: t.accent,
                cursorWidth: 1.4,
                onSubmitted: (_) => _submit(),
                decoration: InputDecoration(
                  isDense: true,
                  contentPadding: EdgeInsets.zero,
                  border: InputBorder.none,
                  hintText: widget.hint,
                  hintStyle: fieldHintStyle(t.textTertiary),
                ),
              ),
            ),
          ),
        ),
      ),
      actions: [
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

/// 一句说明 + 确定 / 取消；确定可以是灰的（只剩一份配置时删不掉）。
class _ConfirmDialog extends StatelessWidget {
  const _ConfirmDialog({
    required this.title,
    required this.body,
    required this.confirmLabel,
    required this.enabled,
  });

  final String title;
  final String body;
  final String confirmLabel;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    return AlertDialog(
      backgroundColor: t.surface,
      title: Text(
        title,
        style: TextStyle(fontSize: 13, color: t.textSecondary),
      ),
      content: SizedBox(
        width: 280,
        child: Text(
          body,
          style: TextStyle(
            fontSize: 11.5,
            height: 1.5,
            color: enabled ? t.textPrimary : t.textTertiary,
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: enabled
              ? () => Navigator.of(context).pop(true)
              : null,
          child: Text(
            confirmLabel,
            style: TextStyle(fontSize: 11.5, color: t.portNegative),
          ),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: Text(
            '取消',
            style: TextStyle(fontSize: 11.5, color: t.textTertiary),
          ),
        ),
      ],
    );
  }
}
