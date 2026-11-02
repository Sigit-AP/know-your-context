// Classes with properties, construction and statics.
public class Point : Object {
    public int x { get; set; default = 0; }
    public int y { get; set; default = 0; }

    public Point(int x = 0, int y = 0) {
        this.x = x;
        this.y = y;
    }

    public void move(int dx, int dy) {
        x += dx;
        y += dy;
    }

    public bool is_origin() {
        return x == 0 && y == 0;
    }

    public static Point build() {
        return new Point();
    }
}
