// Static vars, inline constants and init.
class Registry {
    public static inline var LIMIT:Int = 100;

    static var counter:Int = 0;

    static function __init__():Void {
        counter = Std.int(LIMIT / 2);
    }

    public static function next():Int {
        counter += 1;
        return counter;
    }
}
