import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../chrome/page_kit.dart';
import '../chrome/primitives.dart';
import '../engine/engine_state.dart';

/// 字节数 → 「53.0 MB」。
String formatBytes(int bytes, {int decimals = 1}) {
  if (bytes <= 0) return '--';
  if (bytes >= 1 << 30) {
    return '${(bytes / (1 << 30)).toStringAsFixed(decimals)} GB';
  }
  if (bytes >= 1 << 20) {
    return '${(bytes / (1 << 20)).toStringAsFixed(decimals)} MB';
  }
  return '${(bytes / 1024).toStringAsFixed(0)} KB';
}

/// 启动时发现新版本的弹窗：说清版本、大小与更新内容，按钮直接开始更新。
///
/// Overlay 里拿不到页面的取值环境，弹窗自己带一份令牌（与 README 弹窗同规矩）。
Future<void> showUpdatePrompt(
  BuildContext context,
  StudioEngine engine, {
  required AppTokens tokens,
  required List<String> palette,
  required VoidCallback onUpdate,
}) async {
  final info = engine.updateInfo;
  if (info == null || info['has_update'] != true) return;
  await showDialog<void>(
    context: context,
    builder: (dialogContext) => AppScope(
      tokens: tokens,
      palette: palette,
      child: _UpdatePrompt(
        info: info,
        onUpdate: () {
          Navigator.of(dialogContext).pop();
          onUpdate();
        },
      ),
    ),
  );
}

class _UpdatePrompt extends StatelessWidget {
  const _UpdatePrompt({required this.info, required this.onUpdate});

  final Map<String, Object?> info;
  final VoidCallback onUpdate;

  @override
  Widget build(BuildContext context) {
    final t = AppScope.of(context);
    final notes = '${info['notes'] ?? ''}'.trim();
    final asset = info['asset'] is Map
        ? Map<String, Object?>.from(info['asset'] as Map)
        : const <String, Object?>{};
    final size = (asset['size'] as num?)?.toInt() ?? 0;
    return Dialog(
      backgroundColor: t.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppTokens.radiusCard),
        side: BorderSide(color: t.edge),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480, maxHeight: 520),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  Text(
                    '发现新版本 v${info['latest'] ?? ''}',
                    style: TextStyle(
                      fontSize: 15,
                      height: 1,
                      fontWeight: FontWeight.w600,
                      letterSpacing: -.2,
                      color: t.textPrimary,
                    ),
                  ),
                  const Spacer(),
                  StatusChip(text: '可更新', tone: 1),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                '当前 v${info['current'] ?? ''}'
                '${size > 0 ? ' · 安装包 ${formatBytes(size)}' : ''}',
                style: TextStyle(
                  fontFamily: 'Consolas',
                  fontSize: 10.5,
                  height: 1.3,
                  color: t.textSecondary,
                ),
              ),
              if (notes.isNotEmpty) ...[
                const SizedBox(height: 10),
                WellBox(
                  padding: const EdgeInsets.fromLTRB(9, 7, 9, 7),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 150),
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
              const SizedBox(height: 10),
              Text(
                '更新会先核对 SHA-256 与发布签名，再替换程序并自动重启；'
                '配置与模块不受影响',
                style: TextStyle(
                  fontSize: 9.5,
                  height: 1.35,
                  color: t.textTertiary,
                ),
              ),
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  GhostButton(
                    key: const ValueKey('update-later'),
                    label: '稍后',
                    dense: true,
                    onTap: () => Navigator.of(context).pop(),
                  ),
                  const SizedBox(width: 8),
                  GhostButton(
                    key: const ValueKey('update-now'),
                    label: '立即更新',
                    icon: Icons.system_update_alt_rounded,
                    dense: true,
                    active: true,
                    onTap: onUpdate,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
