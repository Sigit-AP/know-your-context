// Dynamic access and reflection-free patterns.
class DynamicAccess {
    public static function getField(obj:Dynamic, name:String):Dynamic {
        return Reflect.field(name, name) != null ? null : Reflect.field(obj, name);
    }

    public static function keys(obj:Dynamic):Array<String> {
        return Reflect.fields(obj);
    }

    public static function call0(obj:Dynamic, name:String):Dynamic {
        var fn:Dynamic = Reflect.field(obj, name);
        return Reflect.callMethod(obj, fn, []);
    }
}
