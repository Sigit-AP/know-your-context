# Classes with properties, constructors and methods.
class Point {
    [int]$X = 0
    [int]$Y = 0

    Point() {
    }

    Point([int]$x, [int]$y) {
        $this.X = $x
        $this.Y = $y
    }

    [void] Move([int]$dx, [int]$dy) {
        $this.X += $dx
        $this.Y += $dy
    }

    [bool] IsOrigin() {
        return $this.X -eq 0 -and $this.Y -eq 0
    }

    static [object] Build() {
        return [Point]::new()
    }
}
