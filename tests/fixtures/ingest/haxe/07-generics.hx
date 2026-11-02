// Generic classes and functions with constraints.
class Box<T> {
    private var item:T;

    public function new(item:T) {
        this.item = item;
    }

    public function unwrap():T {
        return item;
    }
}

class Util {
    public static function identity<T>(value:T):T {
        return value;
    }

    public static function first<T>(items:Array<T>):Null<T> {
        return items.length > 0 ? items[0] : null;
    }
}
