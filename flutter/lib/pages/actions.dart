/// 页面与外壳之间唯一的通道：页面只发起引擎调用与提示，不碰画布控制器。
class StudioActions {
  const StudioActions({
    required this.invoke,
    required this.toast,
    required this.spawnType,
    required this.selectSlot,
  });

  /// 调用桥上的一个引擎方法，失败由外壳统一回落成提示。
  /// 取市场清单这类要几十秒的调用自己放宽 [timeout]，别等外壳的默认值。
  final Future<Object?> Function(
    String method, [
    Map<String, Object?>? args,
    Duration? timeout,
  ])
  invoke;

  final void Function(String message) toast;

  /// 在画布上放一个某个类型的节点（事件流页用）。
  final void Function(String type) spawnType;

  final void Function(String slotId) selectSlot;
}
