// Metadata on classes and fields.
@:keep
@:expose("DemoService")
class Service {
    @:deprecated("use handle2")
    public function handle():Void {
    }

    @:final
    public function handle2():Void {
    }
}
