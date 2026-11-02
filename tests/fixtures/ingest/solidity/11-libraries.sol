// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

library StringLib {
    function equals(string memory a, string memory b) internal pure returns (bool) {
        return keccak256(bytes(a)) == keccak256(bytes(b));
    }

    function isEmpty(string memory value) internal pure returns (bool) {
        return bytes(value).length == 0;
    }
}

library ArrayLib {
    function sum(uint256[] memory values) internal pure returns (uint256 total) {
        for (uint256 i = 0; i < values.length; i++) {
            total += values[i];
        }
    }
}
