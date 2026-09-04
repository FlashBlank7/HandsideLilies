import QtQml

QtObject {
    // angleDelta uses eighth-degrees: 120 units are one ordinary wheel notch.
    // Keep sub-notch packets instead of multiplying a high-resolution wheel's
    // sensitivity by the number of packets the driver happens to emit.
    property real remainder: 0

    function consume(angleDelta) {
        var delta = Number(angleDelta)
        if (!isFinite(delta) || delta === 0)
            return 0
        var total = remainder + delta
        var magnitude = Math.floor(Math.abs(total) / 120 + 0.000000001)
        var steps = total < 0 ? -magnitude : magnitude
        remainder = total - steps * 120
        if (Math.abs(remainder) < 0.0000001)
            remainder = 0
        return steps
    }
}
