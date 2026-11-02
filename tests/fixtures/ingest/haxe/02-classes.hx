// Classes with fields, new and inheritance.
class Point {
    public var x:Int;
    public var y:Int;

    public function new(?x:Int = 0, ?y:Int = 0) {
        this.x = x;
        this.y = y;
    }

    public function move(dx:Int, dy:Int):Point {
        x += dx;
        y += dy;
        return this;
    }

    public function origin():Bool {
        return x == 0 && y == 0;
    }
}

class NamedPoint extends Point {
    public var label:String;

    public function new(label:String) {
        super();
        this.label = label;
    }
}
