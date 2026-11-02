// Call expressions parking unresolved refs.
class Calls {
    static function helper(value:Int):Int {
        return value * 2;
    }

    public static function run(seed:Int):Int {
        var total = helper(seed);
        return helper(total);
    }
}
