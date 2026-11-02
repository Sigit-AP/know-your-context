// Interfaces and typedef structural types.
interface Shape {
    public function area():Float;
    public var name(get, never):String;
}

typedef SizeT = {
    var width:Int;
    var height:Int;
}

class Circle implements Shape {
    public var radius:Float = 1.0;

    public var name(get, never):String;

    public function new() {}

    function get_name():String {
        return "circle";
    }

    public function area():Float {
        return 3.14159 * radius * radius;
    }
}
