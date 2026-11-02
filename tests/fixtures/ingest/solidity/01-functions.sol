// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Basic free functions and library functions.
library MathLib {
    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        return a + b;
    }

    function clamp(uint256 value, uint256 low, uint256 high) internal pure returns (uint256) {
        if (value < low) return low;
        if (value > high) return high;
        return value;
    }
}

function addFree(uint256 a, uint256 b) pure returns (uint256) {
    return a + b;
}
